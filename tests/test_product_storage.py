from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from nonocode.agent import (ErrorEvent, LoopComplete, PermissionRequest, PermissionResponse,
                            ToolResultEvent, ToolUseEvent, UsageEvent)
from nonocode.conversation import Message
from nonocode.memory.auto_memory import MemoryManager
from nonocode.memory.session import SessionManager, SessionMeta, SessionRecord
from nonocode.product import ProductService
from nonocode.storage import Database, default_database_path
from nonocode.storage.runs import TrackedRun
from nonocode.storage.database import preferred_journal_mode


def test_database_is_local_and_configurable(tmp_path, monkeypatch):
    monkeypatch.delenv('NONOCODE_DATA_DIR')
    monkeypatch.setattr(Path,'home',lambda: tmp_path/'user-home')
    assert default_database_path() == tmp_path/'user-home'/'.local/share/nonocode/nonocode.db'
    monkeypatch.setenv('NONOCODE_DATA_DIR',str(tmp_path/'custom'))
    database = Database(str(tmp_path/'workspace'))
    assert database.path == tmp_path/'custom/nonocode.db'
    with database.connect() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == preferred_journal_mode()
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 2


def test_future_database_schema_is_not_downgraded(tmp_path):
    path = tmp_path/'future.db'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError,match='升级'):
        Database(str(tmp_path),path)


def test_import_legacy_is_idempotent_and_keeps_source(tmp_path):
    directory = tmp_path/'.nonocode/sessions'
    directory.mkdir(parents=True)
    meta = SessionMeta(id='legacy_123',title='Legacy conversation',message_count=1)
    meta.save(directory/'legacy_123.meta')
    line = SessionRecord.from_message(Message(role='user',content='旧会话关键词'))[0].to_jsonl()
    transcript = directory/'legacy_123.jsonl'
    transcript.write_text(line+'\n{broken\n')
    manager = SessionManager(str(tmp_path))
    for _ in range(3):
        resumed = SessionManager(str(tmp_path)).resume(meta.id)
        assert [m.content for m in resumed.messages] == ['旧会话关键词']
        resumed.session.close()
    assert transcript.read_text() == line+'\n{broken\n'
    assert len(manager.database.records(meta.id)) == 1


def test_concurrent_import_does_not_duplicate_records(tmp_path):
    database = Database(str(tmp_path))
    meta = SessionMeta(id='imported').to_dict()
    record = json.loads(SessionRecord.from_message(Message(role='user',content='once'))[0].to_jsonl())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: database.import_session(meta,[record]),range(4)))
    assert sum(results) == 1
    assert len(database.records('imported')) == 1


def test_resume_uses_database_after_exports_are_deleted(tmp_path):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message(role='user',content='Persistent content'))
    session.close()
    for suffix in ('.meta','.jsonl'):
        (manager._sessions_dir/(session.session_id+suffix)).unlink()
    resumed = SessionManager(str(tmp_path)).resume(session.session_id)
    assert resumed.messages[0].content == 'Persistent content'
    resumed.session.close()


def test_deleted_import_is_not_resurrected(tmp_path):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message(role='user',content='delete me'))
    session.close()
    meta = (manager._sessions_dir/(session.session_id+'.meta')).read_bytes()
    transcript = (manager._sessions_dir/(session.session_id+'.jsonl')).read_bytes()
    assert manager.delete(session.session_id)
    (manager._sessions_dir/(session.session_id+'.meta')).write_bytes(meta)
    (manager._sessions_dir/(session.session_id+'.jsonl')).write_bytes(transcript)
    assert SessionManager(str(tmp_path)).resume(session.session_id) is None


@pytest.mark.parametrize('query',['中国','中国研究','search phrase','100%','"quoted"'])
def test_search_chinese_literals_and_workspace_isolation(tmp_path,query):
    first = SessionManager(str(tmp_path/'first'))
    session = first.create()
    session.append(Message(role='user',content='中国研究 search phrase 100% "quoted"'))
    session.close()
    assert first.search(query)[0]['session_id'] == session.session_id
    second = SessionManager(str(tmp_path/'second'))
    assert second.search(query) == []
    assert second.resume(session.session_id) is None


def test_search_returns_distinct_sessions_when_one_has_many_matches(tmp_path):
    manager = SessionManager(str(tmp_path))
    first = manager.create()
    first.append(Message(role='user',content='matching keyword'))
    first.close()
    second = manager.create()
    for _ in range(110):
        second.append(Message(role='user',content='matching keyword'))
    second.close()
    assert len(manager.search('keyword',2)) == 2


def test_session_ids_cannot_escape_workspace(tmp_path):
    manager = SessionManager(str(tmp_path))
    assert manager.resume('../outside') is None
    assert not manager.delete('../outside')


def test_consistent_backup_includes_uncheckpointed_data(tmp_path):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message(role='user',content='backup content'))
    session.close()
    backup = manager.database.backup(tmp_path/'backup.db')
    restored = Database(str(tmp_path),backup)
    assert restored.records(session.session_id)[0]['content'] == 'backup content'
    with pytest.raises(ValueError):
        manager.database.backup(backup)


def test_memory_crud_rebuilds_index_and_preserves_unrelated_index_text(tmp_path):
    manager = SessionManager(str(tmp_path))
    memories = MemoryManager(str(tmp_path))
    service = ProductService(manager,memories)
    memories.project_mem_dir.mkdir(parents=True)
    memories.project_path.write_text('Manual index introduction\n')
    entry = service.update_memory('project','language','请使用中文',create=True)
    assert entry['body'] == '请使用中文'
    assert service.memory_entries('中文')[0]['name'] == 'language'
    service.update_memory('project','language','Use concise answers')
    assert 'Manual index introduction' in memories.project_path.read_text()
    assert memories.project_path.read_text().count('(language.md)') == 1
    assert service.memory_entries('中文') == []
    service.forget_memory('project','language')
    assert service.memory_entries() == []
    assert 'language.md' not in memories.project_path.read_text()


def test_memory_index_reflects_external_markdown_edits(tmp_path):
    service = ProductService(SessionManager(str(tmp_path)),MemoryManager(str(tmp_path)))
    entry = service.update_memory('project','facts','original',create=True)
    Path(entry['path']).write_text('---\nname: facts\ntype: project\n---\nExternal change')
    assert service.memory_entries('External')[0]['name'] == 'facts'
    assert service.memory_entries('original') == []


@pytest.mark.parametrize('scope,name',[('invalid','name'),('project','../outside'),('user','MEMORY')])
def test_memory_operations_validate_scope_and_path(tmp_path,scope,name):
    service = ProductService(SessionManager(str(tmp_path)),MemoryManager(str(tmp_path)))
    with pytest.raises(ValueError):
        service.update_memory(scope,name,'content',create=True)


def test_memory_symlink_is_not_followed(tmp_path):
    service = ProductService(SessionManager(str(tmp_path)),MemoryManager(str(tmp_path)))
    directory = service.memories.project_mem_dir
    directory.mkdir(parents=True)
    outside = tmp_path/'outside.txt'
    outside.write_text('untouched')
    (directory/'evil.md').symlink_to(outside)
    with pytest.raises(ValueError):
        service.update_memory('project','evil','overwritten')
    assert outside.read_text() == 'untouched'


@pytest.mark.asyncio
async def test_tracked_run_records_permission_step_usage_and_completion(tmp_path):
    database = Database(str(tmp_path))
    run = TrackedRun(database,'session','Read file','test',input_offset=100,output_offset=20)
    future = asyncio.get_running_loop().create_future()
    async def events():
        yield ToolUseEvent('ReadFile','read',{'file_path':'input.txt'})
        yield PermissionRequest('ReadFile','input.txt',future)
        await future
        yield ToolResultEvent('read','ReadFile','file content',False,0.1)
        yield UsageEvent(110,23)
        yield LoopComplete(1)
    async for event in run.events(events()):
        if isinstance(event,PermissionRequest):
            assert database.get_run(run.id)['status'] == 'waiting_permission'
            future.set_result(PermissionResponse.ALLOW)
    saved = database.get_run(run.id)
    assert saved['status'] == 'completed'
    assert saved['input_tokens'] == 10 and saved['output_tokens'] == 3
    assert saved['steps'][0]['status'] == 'completed'
    assert saved['steps'][0]['output'] == 'file content'


@pytest.mark.asyncio
async def test_failed_run_preserves_error_and_does_not_replay(tmp_path):
    database = Database(str(tmp_path))
    run = TrackedRun(database,'session','task','test')
    async def events():
        yield ToolUseEvent('WriteFile','write',{})
        raise RuntimeError('disk failure')
    with pytest.raises(RuntimeError):
        async for _ in run.events(events()):
            pass
    saved = database.get_run(run.id)
    assert saved['status'] == 'failed' and saved['error'] == 'disk failure'
    assert saved['steps'][0]['status'] == 'interrupted'


def test_dead_process_run_recovers_as_interrupted(tmp_path,monkeypatch):
    database = Database(str(tmp_path))
    database.create_run('dead','session','task','test')
    def dead(pid,signal):
        raise ProcessLookupError
    monkeypatch.setattr(os,'kill',dead)
    assert database.list_runs()[0]['status'] == 'interrupted'
    other = Database(str(tmp_path/'other'))
    assert other.get_run('dead') is None


@pytest.mark.asyncio
async def test_tool_ids_reused_in_later_turns_preserve_both_steps(tmp_path):
    database = Database(str(tmp_path))
    run = TrackedRun(database,'session','task','test')
    async def events():
        for output in ('first','second'):
            yield ToolUseEvent('ReadFile','provider-reused-id',{})
            yield ToolResultEvent('provider-reused-id','ReadFile',output,False,0.1)
        yield LoopComplete(2)
    async for _ in run.events(events()):
        pass
    assert [step['output'] for step in database.get_run(run.id)['steps']] == ['first','second']


@pytest.mark.parametrize('version,mode',[((3,51,0),'delete'),((3,51,2),'delete'),
    ((3,51,3),'wal'),((3,50,6),'delete'),((3,50,7),'wal'),((3,44,6),'wal'),((3,52,0),'wal')])
def test_wal_requires_a_version_with_sqlite_race_fix(version,mode):
    assert preferred_journal_mode(version) == mode


def test_non_posix_recovery_does_not_send_process_signals(tmp_path,monkeypatch):
    database = Database(str(tmp_path))
    database.create_run('windows','session','task','test')
    with monkeypatch.context() as temporary:
        temporary.setattr(os,'name','nt')
        temporary.setattr(os,'kill',lambda *args: pytest.fail('must not probe using signals'))
        assert database.recover_interrupted_runs() == 0
