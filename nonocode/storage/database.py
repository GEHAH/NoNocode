from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
import re
from typing import Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_database_path() -> Path:
    override = os.environ.get('NONOCODE_DATA_DIR')
    directory = Path(override).expanduser() if override else Path.home() / '.local' / 'share' / 'nonocode'
    return directory / 'nonocode.db'


def preferred_journal_mode(version: tuple[int, ...] | None = None) -> str:
    """Use WAL only with the SQLite WAL-reset fix (sqlite.org/wal.html#walreset)."""
    version = version or sqlite3.sqlite_version_info
    patched = (version >= (3,51,3) or
               version[:2] == (3,50) and version >= (3,50,7) or
               version[:2] == (3,44) and version >= (3,44,6))
    return 'wal' if patched else 'delete'


class Database:
    """Short transactions on local disk, scoped by resolved workspace path.

    Connections are not shared across threads. Tool/model execution never takes
    place inside a transaction. JSONL files remain portable transcript exports.
    """

    def __init__(self, work_dir: str, path: Path | None = None) -> None:
        self.path = path or default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspace_path = str(Path(work_dir).resolve())
        self.workspace_id = hashlib.sha256(self.workspace_path.encode()).hexdigest()
        with self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise RuntimeError('数据库版本比当前程序更新，请升级 NoNocode')
            self.journal_mode = connection.execute('PRAGMA journal_mode='+preferred_journal_mode()).fetchone()[0]
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS workspaces(
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    workspace_id TEXT NOT NULL, id TEXT NOT NULL, metadata TEXT NOT NULL,
                    last_active TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(workspace_id,id),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id));
                CREATE TABLE IF NOT EXISTS session_records(
                    id INTEGER PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    record TEXT NOT NULL, search_text TEXT NOT NULL,
                    FOREIGN KEY(workspace_id,session_id) REFERENCES sessions(workspace_id,id));
                CREATE INDEX IF NOT EXISTS records_session ON session_records(workspace_id,session_id,id);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
                    error TEXT NOT NULL DEFAULT '', owner_pid INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id));
                CREATE INDEX IF NOT EXISTS runs_workspace ON runs(workspace_id,started_at DESC);
                CREATE TABLE IF NOT EXISTS run_steps(
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, tool_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    output TEXT NOT NULL DEFAULT '', elapsed REAL NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id));
                CREATE TABLE IF NOT EXISTS memory_index(
                    workspace_id TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL,
                    type TEXT NOT NULL, description TEXT NOT NULL, body TEXT NOT NULL,
                    path TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id,scope,name));
            ''')
            connection.execute('INSERT OR IGNORE INTO workspaces VALUES(?,?,?)',
                               (self.workspace_id, self.workspace_path, now()))
            try:
                connection.executescript('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                        search_text, content='session_records', content_rowid='id', tokenize='trigram');
                    CREATE TRIGGER IF NOT EXISTS records_fts_insert AFTER INSERT ON session_records BEGIN
                        INSERT INTO transcript_fts(rowid,search_text) VALUES(new.id,new.search_text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS records_fts_delete AFTER DELETE ON session_records BEGIN
                        INSERT INTO transcript_fts(transcript_fts,rowid,search_text)
                            VALUES('delete',old.id,old.search_text);
                    END;
                ''')
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
        from nonocode.storage.schema import migrate, workspace_identity
        migrate(self)
        self.workspace_id = workspace_identity(self)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _upsert_session(self, connection: sqlite3.Connection, metadata: dict) -> None:
        connection.execute('''INSERT INTO sessions(workspace_id,id,metadata,last_active) VALUES(?,?,?,?)
            ON CONFLICT(workspace_id,id) DO UPDATE SET metadata=excluded.metadata,last_active=excluded.last_active''',
            (self.workspace_id, metadata['id'], json.dumps(metadata, ensure_ascii=False), metadata['last_active']))

    def save_session(self, metadata: dict) -> None:
        with self.connect() as connection:
            self._upsert_session(connection, metadata)

    def import_session(self, metadata: dict, records: list[dict]) -> bool:
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM sessions WHERE workspace_id=? AND id=?',
                                  (self.workspace_id, metadata['id'])).fetchone():
                return False
            self._upsert_session(connection, metadata)
            self._insert_records(connection, metadata['id'], records)
        return True

    def _insert_records(self, connection: sqlite3.Connection, session_id: str, records: list[dict]) -> None:
        for record in records:
            search_text = str(record.get('content', ''))
            if record.get('tool_uses') or record.get('tool_results'):
                search_text += '\n' + json.dumps([record.get('tool_uses'), record.get('tool_results')], ensure_ascii=False)
            connection.execute('INSERT INTO session_records(workspace_id,session_id,record,search_text) VALUES(?,?,?,?)',
                               (self.workspace_id, session_id, json.dumps(record, ensure_ascii=False), search_text))

    def append_records(self, metadata: dict, records: list[dict]) -> None:
        with self.connect() as connection:
            self._upsert_session(connection, metadata)
            self._insert_records(connection, metadata['id'], records)

    def session_metadata(self, session_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT metadata FROM sessions WHERE workspace_id=? AND id=? AND deleted=0',
                                     (self.workspace_id, session_id)).fetchone()
        return json.loads(row['metadata']) if row else None

    def known_sessions(self) -> set[str]:
        with self.connect() as connection:
            return {row['id'] for row in connection.execute('SELECT id FROM sessions WHERE workspace_id=?',
                                                           (self.workspace_id,))}

    def list_sessions(self) -> list[dict]:
        with self.connect() as connection:
            return [json.loads(row['metadata']) for row in connection.execute(
                'SELECT metadata FROM sessions WHERE workspace_id=? AND deleted=0 ORDER BY last_active DESC',
                (self.workspace_id,))]

    def records(self, session_id: str) -> list[dict]:
        with self.connect() as connection:
            return [json.loads(row['record']) for row in connection.execute(
                'SELECT record FROM session_records WHERE workspace_id=? AND session_id=? ORDER BY id',
                (self.workspace_id, session_id))]

    def delete_session(self, session_id: str) -> bool:
        with self.connect() as connection:
            changed = connection.execute('UPDATE sessions SET deleted=1 WHERE workspace_id=? AND id=? AND deleted=0',
                                         (self.workspace_id, session_id)).rowcount
            connection.execute('DELETE FROM session_records WHERE workspace_id=? AND session_id=?',
                               (self.workspace_id, session_id))
        return bool(changed)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        if len(query) > 500:
            raise ValueError('搜索内容最多 500 个字符')
        limit = min(max(limit, 1), 100)
        if self.fts and len(query) >= 3:
            predicate = 'r.id IN (SELECT rowid FROM transcript_fts WHERE transcript_fts MATCH ?)'
            term = '"' + query.replace('"', '""') + '"'
        else:
            predicate = 'instr(lower(r.search_text),lower(?))>0'
            term = query
        with self.connect() as connection:
            rows = connection.execute(f'''WITH matches AS (
                SELECT r.session_id,s.metadata,r.search_text,r.id,
                row_number() OVER(PARTITION BY r.session_id ORDER BY r.id DESC) AS rank
                FROM session_records r
                JOIN sessions s ON s.workspace_id=r.workspace_id AND s.id=r.session_id
                WHERE r.workspace_id=? AND s.deleted=0 AND {predicate})
                SELECT session_id,metadata,search_text FROM matches WHERE rank=1 ORDER BY id DESC LIMIT ?''', (self.workspace_id, term, limit)).fetchall()
        results, seen = [], set()
        for row in rows:
            if row['session_id'] in seen:
                continue
            seen.add(row['session_id'])
            content = row['search_text']
            position = max(content.casefold().find(query.casefold()), 0)
            metadata = json.loads(row['metadata'])
            results.append({'session_id': row['session_id'], 'title': metadata['title'],
                            'last_active': metadata['last_active'],
                            'snippet': content[max(0,position-40):position+200]})
            if len(results) >= limit:
                break
        return results

    def backup(self, destination: Path) -> Path:
        destination = destination.expanduser().resolve()
        if destination == self.path.resolve() or destination.exists():
            raise ValueError('备份目标必须是尚不存在的新文件')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        destination.chmod(0o600)
        return destination

    def create_run(self, run_id: str, session_id: str, prompt: str, source: str) -> None:
        timestamp = now()
        with self.connect() as connection:
            connection.execute('''INSERT INTO runs(id,workspace_id,session_id,prompt,source,status,
                started_at,updated_at,owner_pid) VALUES(?,?,?,?,?,'running',?,?,?)''',
                (run_id,self.workspace_id,session_id,prompt,source,timestamp,timestamp,os.getpid()))

    def update_run(self, run_id: str, status: str, *, error: str = '', input_tokens: int | None = None,
                   output_tokens: int | None = None) -> None:
        if status not in {'running','waiting_permission','completed','failed','cancelled','interrupted'}:
            raise ValueError('无效的任务状态')
        terminal = status in {'completed','failed','cancelled','interrupted'}
        timestamp = now()
        with self.connect() as connection:
            connection.execute('''UPDATE runs SET status=?,updated_at=?,finished_at=?,error=?,
                input_tokens=coalesce(?,input_tokens),output_tokens=coalesce(?,output_tokens)
                WHERE id=? AND workspace_id=?''',
                (status,timestamp,timestamp if terminal else None,error,input_tokens,output_tokens,
                 run_id,self.workspace_id))
            if terminal:
                connection.execute('''UPDATE run_steps SET status='interrupted',finished_at=?
                    WHERE run_id=? AND status IN ('running','waiting_permission')''', (timestamp,run_id))

    def start_step(self, run_id: str, tool_id: str, tool_name: str, arguments: dict) -> None:
        with self.connect() as connection:
            connection.execute('''INSERT INTO run_steps(run_id,tool_id,tool_name,arguments,status,started_at)
                VALUES(?,?,?,?,'running',?)''',
                (run_id,tool_id,tool_name,json.dumps(arguments,ensure_ascii=False),now()))

    def wait_for_permission(self, run_id: str, tool_name: str) -> None:
        with self.connect() as connection:
            connection.execute('''UPDATE run_steps SET status='waiting_permission' WHERE id=(
                SELECT id FROM run_steps WHERE run_id=? AND tool_name=? AND status='running' ORDER BY id LIMIT 1)''',
                (run_id,tool_name))
        self.update_run(run_id,'waiting_permission')

    def finish_step(self, run_id: str, tool_id: str, output: str, is_error: bool, elapsed: float) -> None:
        with self.connect() as connection:
            connection.execute('''UPDATE run_steps SET status=?,output=?,elapsed=?,finished_at=?
                WHERE id=(SELECT id FROM run_steps WHERE run_id=? AND tool_id=?
                AND status IN ('running','waiting_permission') ORDER BY id DESC LIMIT 1)''',
                ('failed' if is_error else 'completed',output[:100_000],elapsed,now(),run_id,tool_id))
        self.update_run(run_id,'running')

    def recover_interrupted_runs(self) -> int:
        """Only mark runs belonging to dead local processes; never replay tools."""
        if os.name != 'posix':
            # os.kill(pid, 0) is not a portable liveness probe on Windows.
            return 0
        with self.connect() as connection:
            rows = connection.execute('''SELECT id,owner_pid FROM runs WHERE workspace_id=?
                AND status IN ('running','waiting_permission')''',(self.workspace_id,)).fetchall()
        recovered = 0
        for row in rows:
            try:
                os.kill(row['owner_pid'],0)
            except ProcessLookupError:
                self.update_run(row['id'],'interrupted',error='进程已退出；可恢复会话，工具不会自动重放')
                recovered += 1
            except PermissionError:
                pass
        return recovered

    def list_runs(self, limit: int = 30) -> list[dict]:
        self.recover_interrupted_runs()
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM runs WHERE workspace_id=? ORDER BY started_at DESC LIMIT ?',
                (self.workspace_id,min(max(limit,1),100)))]

    def get_run(self, run_id: str) -> dict | None:
        self.recover_interrupted_runs()
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM runs WHERE id=? AND workspace_id=?',
                                     (run_id,self.workspace_id)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result['steps'] = [dict(step) for step in connection.execute(
                'SELECT * FROM run_steps WHERE run_id=? ORDER BY id',(run_id,))]
        return result

    def index_memories(self, entries: list[dict]) -> None:
        # This is a disposable index. Markdown remains the memory source of truth.
        with self.connect() as connection:
            connection.execute('DELETE FROM memory_index WHERE workspace_id=?',(self.workspace_id,))
            for entry in entries:
                connection.execute('INSERT INTO memory_index VALUES(?,?,?,?,?,?,?,?)',
                    (self.workspace_id,entry['scope'],entry['name'],entry['type'],entry['description'],
                     entry['body'],entry['path'],entry['updated_at']))

    def search_memories(self, query: str = '') -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute('''SELECT * FROM memory_index WHERE workspace_id=? AND
                instr(lower(name||' '||description||' '||body),lower(?))>0 ORDER BY scope,name''',
                (self.workspace_id,query[:500])).fetchall()
        return [dict(row) for row in rows]
