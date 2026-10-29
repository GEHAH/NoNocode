"""Versioned, transactional upgrades and portable workspace identity."""
from __future__ import annotations
import os
from pathlib import Path
import re
import uuid

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS workspace_aliases(path TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id));
ALTER TABLE runs ADD COLUMN root_run_id TEXT;
ALTER TABLE runs ADD COLUMN parent_run_id TEXT;
CREATE INDEX runs_root ON runs(root_run_id);
CREATE TABLE artifacts(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),relative_path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,media_type TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(workspace_id,relative_path,version));
CREATE TABLE artifact_runs(artifact_id TEXT NOT NULL REFERENCES artifacts(id),run_id TEXT NOT NULL REFERENCES runs(id),PRIMARY KEY(artifact_id,run_id));
CREATE TABLE sources(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),run_id TEXT NOT NULL REFERENCES runs(id),url TEXT NOT NULL,title TEXT NOT NULL,snippet TEXT NOT NULL,sha256 TEXT NOT NULL,retrieved_at TEXT NOT NULL,kind TEXT NOT NULL,truncated INTEGER NOT NULL DEFAULT 0);
CREATE INDEX sources_run ON sources(workspace_id,run_id);
CREATE TABLE usage_events(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),run_id TEXT NOT NULL REFERENCES runs(id),root_run_id TEXT NOT NULL,phase TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,cache_read INTEGER NOT NULL DEFAULT 0,cache_creation INTEGER NOT NULL DEFAULT 0,total_tokens INTEGER NOT NULL DEFAULT 0,cost_usd REAL,pricing TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,elapsed REAL NOT NULL DEFAULT 0,day_key TEXT NOT NULL);
CREATE INDEX usage_day ON usage_events(day_key);
CREATE INDEX usage_root ON usage_events(root_run_id);
CREATE TABLE memory_evidence(workspace_id TEXT NOT NULL,scope TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT NOT NULL,source TEXT NOT NULL,evidence TEXT NOT NULL,session_id TEXT NOT NULL DEFAULT '',expires_at TEXT NOT NULL DEFAULT '',verified_at TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,PRIMARY KEY(workspace_id,scope,name));
CREATE TABLE memory_usage(id INTEGER PRIMARY KEY,workspace_id TEXT NOT NULL,run_id TEXT NOT NULL,scope TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE settings_audit(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,revision TEXT NOT NULL,changed_fields TEXT NOT NULL,created_at TEXT NOT NULL);
"""


def migrate(database):
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA_VERSION:
            raise RuntimeError('数据库版本比当前程序更新，请升级 NoNocode')
        if version<2:
            for statement in DDL.split(';'):
                if statement.strip():connection.execute(statement)
            connection.execute('UPDATE runs SET root_run_id=id WHERE root_run_id IS NULL')
            connection.execute('PRAGMA user_version=2')


def _workspace_identity_unlocked(database):
    root=Path(database.workspace_path)
    if not root.is_dir():return database.workspace_id
    directory=root/'.nonocode';identity=directory/'workspace-id'
    if directory.is_symlink() or identity.is_symlink():
        raise ValueError('拒绝符号链接工作区标识')
    try:
        value=identity.read_text().strip() if identity.exists() else database.workspace_id
        if not re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{64}',value):raise ValueError('工作区标识损坏')
        with database.connect() as connection:
            previous=connection.execute('SELECT path FROM workspaces WHERE id=?',(value,)).fetchone()
            # A copied checkout gets a new identity while a moved checkout keeps history.
            if previous and previous['path']!=database.workspace_path and Path(previous['path']).exists():
                value=uuid.uuid4().hex
            connection.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?,datetime('now'))",(value,database.workspace_path))
            connection.execute('UPDATE workspaces SET path=? WHERE id=?',(database.workspace_path,value))
            connection.execute('INSERT OR REPLACE INTO workspace_aliases VALUES(?,?)',(database.workspace_path,value))
            if value!=database.workspace_id:
                tables=['sessions','runs','artifacts','sources','usage_events','memory_evidence','memory_usage','settings_audit']
                empty=not any(connection.execute(f'SELECT 1 FROM {table} WHERE workspace_id=? LIMIT 1',(database.workspace_id,)).fetchone() for table in tables)
                if empty:
                    connection.execute('DELETE FROM workspaces WHERE id=? AND id!=?',(database.workspace_id,value))
        directory.mkdir(parents=True,exist_ok=True)
        if not identity.exists() or identity.read_text().strip()!=value:
            temporary=directory/('.'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(value+'\n');temporary.chmod(0o600);os.replace(temporary,identity)
            finally:temporary.unlink(missing_ok=True)
        return value
    except PermissionError:
        return database.workspace_id


import threading
_identity_lock=threading.RLock()

def workspace_identity(database):
    root=Path(database.workspace_path)
    if not root.is_dir():return database.workspace_id
    directory=root/'.nonocode'
    if directory.is_symlink():raise ValueError('拒绝符号链接配置目录')
    try:
        directory.mkdir(exist_ok=True)
        path=directory/'workspace.lock'
        if path.is_symlink():raise ValueError('拒绝符号链接工作区锁')
        with _identity_lock, path.open('a') as handle:
            try:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX)
            except ImportError:pass
            os.chmod(path,0o600)
            return _workspace_identity_unlocked(database)
    except PermissionError:return database.workspace_id
