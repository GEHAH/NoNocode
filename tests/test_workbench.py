from __future__ import annotations
import asyncio
import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import zipfile

import pytest
from pydantic import BaseModel

from nonocode.agent import Agent,ErrorEvent,LoopComplete
from nonocode.client import LLMClient
from nonocode.config import ProviderConfig
from nonocode.conversation import ConversationManager,Message
from nonocode.execution import current_execution,usage_summary
from nonocode.limits import RunLimits
from nonocode.memory.auto_memory import MemoryManager
from nonocode.memory.session import SessionManager
from nonocode.product import ProductService
from nonocode.settings import SettingsService,KEEP
from nonocode.storage.database import Database
from nonocode.storage.products import ProductStore
from nonocode.storage.runs import TrackedRun
from nonocode.tools import ToolRegistry,create_default_registry
from nonocode.tools.base import TextDelta,StreamEnd,ToolCallComplete,Tool,ToolResult
from test_agent import MockLLMClient

class Empty(BaseModel):
    pass


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    root=tmp_path/'workspace';root.mkdir()
    home=tmp_path/'home';home.mkdir();monkeypatch.setattr(Path,'home',lambda:home)
    monkeypatch.chdir(root)
    manager=SessionManager(str(root));session=manager.create()
    yield root,home,manager,session
    session.close()


async def execute(manager,session,agent,prompt='go'):
    conv=ConversationManager();conv.add_user_message(prompt)
    run=TrackedRun(manager.database,session.session_id,prompt,'test')
    events=[event async for event in run.events(agent.run(conv))]
    return run,events


def test_workspace_move_and_copy_keep_correct_histories(workspace,tmp_path):
    root,home,manager,session=workspace
    session.append(Message(role='user',content='Keep this history'))
    original=manager.database.workspace_id;database_path=manager.database.path
    session.close();moved=tmp_path/'moved';root.rename(moved)
    db=Database(str(moved),path=database_path)
    assert db.workspace_id==original and db.list_sessions()
    cloned=tmp_path/'cloned';shutil.copytree(moved,cloned)
    clone_db=Database(str(cloned),path=database_path)
    assert clone_db.workspace_id!=original and clone_db.list_sessions()==[]
    assert Database(str(moved),path=database_path).workspace_id==original


def test_concurrent_workspace_identity_creation(workspace):
    from concurrent.futures import ThreadPoolExecutor
    root,_,manager,_=workspace
    def open_db(_):return Database(str(root),path=manager.database.path).workspace_id
    with ThreadPoolExecutor(max_workers=4) as pool:identities=list(pool.map(open_db,range(8)))
    assert len(set(identities))==1


@pytest.mark.asyncio
async def test_artifacts_sources_versions_and_export_are_immutable(workspace):
    root,_,manager,session=workspace
    class Fetch(Tool):
        name='WebFetch';description='controlled page';params_model=Empty
        async def execute(self,params):return ToolResult(json.dumps({'url':'https://example.com/research','title':'Evidence','text':'Original evidence','retrieved_at':'2026-10-29T00:00:00Z'}))
    registry=create_default_registry();registry.register(Fetch())
    client=MockLLMClient([[ToolCallComplete('write','WriteFile',{'file_path':str(root/'report.md'),'content':'First version'}),
                          ToolCallComplete('fetch','WebFetch',{}),StreamEnd('tool_use',10,2)],
                         [TextDelta('Final report'),StreamEnd('end_turn',10,2)]])
    run,events=await execute(manager,session,Agent(client,registry,'anthropic',work_dir=str(root)))
    assert any(isinstance(event,LoopComplete) for event in events)
    products=ProductStore(manager.database);artifacts=products.artifacts(run_id=run.id)
    assert len(artifacts)==2
    report=next(item for item in artifacts if item['relative_path']=='report.md')
    (root/'report.md').write_text('Second version')
    assert products.preview(report['id'])['text']=='First version'
    second=products.capture_file(run.id,'report.md');assert second['version']==2
    assert '-First version' in products.diff(report['id'],second['id'])['text']
    sources=products.sources(run_id=run.id);assert len(sources)==1
    assert products.source(sources[0]['id'])['text']=='Original evidence'
    exported=products.download(report['id'],bundle=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported['base64']))) as archive:
        assert archive.read('deliverable/report.md')==b'First version'
        assert json.loads(archive.read('sources.json'))[0]['url']=='https://example.com/research'
    with pytest.raises(ValueError):products.capture_file(run.id,'../outside')
    (root/'.private').write_text('secret')
    with pytest.raises(ValueError):products.capture_file(run.id,'.private')


@pytest.mark.asyncio
async def test_sdk_usage_includes_cache_cost_and_pricing_snapshot(workspace):
    root,_,manager,session=workspace
    provider=ProviderConfig('test','anthropic','https://example.com','controlled',pricing={
        'input_per_million':1,'output_per_million':2,'cache_read_per_million':.1,'cache_creation_per_million':1.2,'as_of':'2026-10-29'})
    class Client(LLMClient):
        _usage_config=provider
        async def stream(self,*args,**kwargs):
            yield TextDelta('answer')
            yield StreamEnd('end_turn',100,50,cache_read=20,cache_creation=10)
    run,_=await execute(manager,session,Agent(Client(),ToolRegistry(),'anthropic',work_dir=str(root)))
    summary=usage_summary(manager.database,run.id)
    assert summary['total_tokens']==180
    assert summary['estimated_cost_usd']==pytest.approx(.000214)
    assert json.loads(summary['calls'][0]['pricing'])['as_of']=='2026-10-29'
    assert current_execution.get() is None


@pytest.mark.asyncio
async def test_parent_and_child_share_token_and_tool_budgets(workspace):
    root,_,manager,session=workspace
    class Child(Tool):
        name='Child';description='child';params_model=Empty
        async def execute(self,params):
            client=MockLLMClient([[TextDelta('child reply'),StreamEnd('end_turn',12,1)]])
            return ToolResult(await Agent(client,ToolRegistry(),'anthropic',work_dir=str(root)).run_to_completion('child task'))
    registry=ToolRegistry();registry.register(Child())
    client=MockLLMClient([[ToolCallComplete('child-call','Child',{}),StreamEnd('tool_use',12,1)],
                          [TextDelta('after child'),StreamEnd('end_turn',12,1)]])
    agent=Agent(client,registry,'anthropic',work_dir=str(root),run_limits=RunLimits(max_tokens=25))
    run,events=await execute(manager,session,agent)
    assert any(isinstance(event,ErrorEvent) and '预算' in event.message for event in events)
    summary=usage_summary(manager.database,run.id)
    assert summary['total_tokens']==26 and len(summary['calls'])==2
    assert len(summary['runs'])==2
    child=next(item for item in summary['runs'] if item['id']!=run.id)
    assert child['parent_run_id']==run.id and child['status']=='failed'
    assert manager.database.get_run(run.id)['status']=='failed'


@pytest.mark.asyncio
async def test_background_memory_is_accounted_after_foreground_completion(workspace):
    root,_,manager,session=workspace
    client=MockLLMClient([[TextDelta('answer'),StreamEnd('end_turn',10,1)],
                         [TextDelta('NONE'),StreamEnd('end_turn',3,1)]])
    memory=MemoryManager(str(root));agent=Agent(client,ToolRegistry(),'anthropic',work_dir=str(root),memory_manager=memory)
    agent._consolidator=None
    run,events=await execute(manager,session,agent)
    await agent.wait_for_memory_extractions()
    summary=usage_summary(manager.database,run.id)
    assert summary['total_tokens']==15
    assert {item['phase'] for item in summary['calls']}=={'agent','memory_extraction'}


@pytest.mark.asyncio
async def test_money_budget_requires_explicit_pricing_before_model_call(workspace):
    root,_,manager,session=workspace
    client=MockLLMClient([[TextDelta('should not execute'),StreamEnd('end_turn',1,1)]])
    run,events=await execute(manager,session,Agent(client,ToolRegistry(),'anthropic',work_dir=str(root),run_limits=RunLimits(max_cost_usd=1)))
    assert client._call_index==0
    assert any(isinstance(event,ErrorEvent) and '价格' in event.message for event in events)


def test_settings_redaction_revision_and_partial_save(workspace):
    root,_,manager,session=workspace
    path=root/'.nonocode/config.yaml'
    path.write_text('providers:\n- name: initial\n  protocol: anthropic\n  base_url: https://example.com\n  model: controlled\n  api_key: private-test-secret\nenable_fork: false\n')
    settings=SettingsService(root,manager.database);first=settings.read()
    assert 'private-test-secret' not in json.dumps(first) and first['values']['providers'][0]['api_key']==KEEP
    result=settings.save({'web':{'enabled':False}},first['revision'])
    assert settings.config().providers[0].api_key=='private-test-secret'
    assert settings.config().enable_fork is False
    assert result['values']['web']['enabled'] is False
    with pytest.raises(ValueError,match='其他窗口'):settings.save({'web':{'enabled':True}},first['revision'])
    with manager.database.connect() as connection:
        audit=connection.execute('SELECT * FROM settings_audit').fetchall()
    assert len(audit)==1 and 'private-test-secret' not in str([tuple(row) for row in audit])
    assert (root/'.nonocode/config.local.yaml').stat().st_mode & 0o777==0o600


def test_memory_provenance_conflicts_expiry_and_index_receipts(workspace):
    root,_,manager,session=workspace
    service=ProductService(manager,MemoryManager(str(root)))
    session.append(Message(role='user',content='请用中文回答'))
    first=service.update_memory('project','language','Use Chinese',create=True,evidence='用中文回答',session_id=session.session_id,topic='language')
    second=service.update_memory('user','global-language','Use English',create=True,topic='language')
    assert first['source']=='manual' and first['evidence']=='用中文回答'
    assert service.memory('project','language')['conflicts']==['user/global-language']
    with pytest.raises(ValueError,match='核对'):service.update_memory('project','language','wrong',evidence='not in conversation',session_id=session.session_id)
    service.update_memory('project','language','Use Chinese',expires_at='2000-01-01T00:00:00Z')
    assert not service.memory('project','language')['active']
    prompt=service.memories.load()
    assert '[language](language.md)' not in prompt and 'global-language.md' in prompt
    service.update_memory('project','language','Use Chinese',expires_at='',enabled=False)
    assert not service.memory('project','language')['active']


@pytest.mark.asyncio
async def test_memory_access_receipts_distinguish_index_from_body(workspace):
    root,_,manager,session=workspace
    memory=MemoryManager(str(root));service=ProductService(manager,memory)
    service.update_memory('project','language','Use Chinese',create=True)
    client=MockLLMClient([[ToolCallComplete('read','ReadFile',{'file_path':str(memory.project_mem_dir/'language.md')}),StreamEnd('tool_use',1,1)],
                         [TextDelta('ok'),StreamEnd('end_turn',1,1)], [TextDelta('NONE'),StreamEnd('end_turn',1,1)]])
    agent=Agent(client,create_default_registry(),'anthropic',work_dir=str(root),memory_manager=memory);agent._consolidator=None
    run,_=await execute(manager,session,agent);await agent.wait_for_memory_extractions()
    from nonocode.memory.provenance import usage
    receipts=usage(manager.database,run.id)
    assert {item['kind'] for item in receipts}=={'index','body'}
    assert all(item['name']=='language' for item in receipts)


@pytest.mark.asyncio
async def test_recovery_bundle_includes_new_products_sources_and_usage(workspace,tmp_path):
    root,_,manager,session=workspace
    run,_=await execute(manager,session,Agent(MockLLMClient([[TextDelta('Snapshot report'),StreamEnd('end_turn',3,1)]]),ToolRegistry(),'anthropic',work_dir=str(root)))
    store=ProductStore(manager.database);artifact=store.artifacts(run_id=run.id)[0]
    store.record_source(run.id,{'url':'https://example.com','text':'Evidence'},'page')
    from nonocode.storage.recovery import backup_bundle,restore_bundle
    archive=backup_bundle(manager.database,tmp_path/'bundle.zip')
    destination=restore_bundle(archive,tmp_path/'restored')
    restored=Database(str(destination/'project'),path=destination/'database/nonocode.db')
    assert ProductStore(restored).preview(artifact['id'])['text']=='Snapshot report'
    assert ProductStore(restored).sources()[0]['url']=='https://example.com'
    assert usage_summary(restored)['total_tokens']==4


def test_real_v1_database_migrates_without_losing_records(tmp_path):
    root=tmp_path/'legacy';root.mkdir();path=tmp_path/'legacy.db'
    identity=hashlib.sha256(str(root).encode()).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parent/'fixtures/schema_v1.sql').read_text())
        connection.execute('INSERT INTO workspaces VALUES(?,?,?)',(identity,str(root),'2026-01-01'))
        meta=json.dumps({'id':'legacy-session','title':'Retained','last_active':'2026-01-01'})
        connection.execute('INSERT INTO sessions VALUES(?,?,?,?,0)',(identity,'legacy-session',meta,'2026-01-01'))
        connection.execute('INSERT INTO session_records(workspace_id,session_id,record,search_text) VALUES(?,?,?,?)',
            (identity,'legacy-session',json.dumps({'role':'user','content':'Keep me'}),'Keep me'))
        connection.execute('INSERT INTO runs(id,workspace_id,session_id,prompt,source,status,started_at,updated_at,owner_pid) VALUES(?,?,?,?,?,?,?,?,?)',
            ('legacy-run',identity,'legacy-session','go','test','completed','2026-01-01','2026-01-01',0))
    database=Database(str(root),path=path)
    assert database.records('legacy-session')[0]['content']=='Keep me'
    assert database.get_run('legacy-run')['root_run_id']=='legacy-run'
    assert database.workspace_id==identity
    with database.connect() as connection:assert connection.execute('PRAGMA user_version').fetchone()[0]==2


def test_failed_migration_rolls_back_schema_changes(tmp_path):
    path=tmp_path/'broken.db';root=tmp_path/'project';root.mkdir()
    with sqlite3.connect(path) as connection:
        connection.executescript((Path(__file__).parent/'fixtures/schema_v1.sql').read_text())
        connection.execute('CREATE TABLE artifacts(bad_column TEXT)')
    with pytest.raises(sqlite3.OperationalError):Database(str(root),path=path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0]==1
        assert 'root_run_id' not in {row[1] for row in connection.execute('PRAGMA table_info(runs)')}


def test_parallel_object_writes_publish_complete_content(workspace):
    from concurrent.futures import ThreadPoolExecutor
    _,_,manager,_=workspace;store=ProductStore(manager.database);data=b'x'*500000
    with ThreadPoolExecutor(max_workers=4) as pool:hashes=list(pool.map(store.put,[data]*8))
    assert len(set(hashes))==1 and store.content(hashes[0])==data


@pytest.mark.asyncio
async def test_daily_token_budget_stops_future_calls_across_root_tasks(workspace):
    root,_,manager,session=workspace
    client=MockLLMClient([[TextDelta('ok'),StreamEnd('end_turn',10,1)]]*4)
    agent=Agent(client,ToolRegistry(),'anthropic',work_dir=str(root),run_limits=RunLimits(max_daily_tokens=20))
    await execute(manager,session,agent)
    second,events=await execute(manager,session,agent)
    assert any(isinstance(event,ErrorEvent) and 'max_daily_tokens' in event.message for event in events)
    third,events=await execute(manager,session,agent)
    assert client._call_index==2
    assert manager.database.get_run(third.id)['status']=='failed'


def test_memory_refresh_replaces_previous_index_context():
    conversation=ConversationManager()
    conversation.inject_long_term_memory('','old-memory.md')
    conversation.add_user_message('Continue')
    conversation.inject_long_term_memory('','new-memory.md')
    assert not any('old-memory.md' in message.content for message in conversation.history)
    assert sum('new-memory.md' in message.content for message in conversation.history)==1
    assert any(message.content=='Continue' for message in conversation.history)


@pytest.mark.asyncio
async def test_background_model_call_obeys_remaining_wall_time(workspace):
    root,_,manager,session=workspace
    class Client(LLMClient):
        calls=0
        async def stream(self,*args,**kwargs):
            self.calls+=1
            if self.calls>1:await asyncio.sleep(30)
            yield TextDelta('NONE');yield StreamEnd('end_turn',1,1)
    agent=Agent(Client(),ToolRegistry(),'anthropic',work_dir=str(root),memory_manager=MemoryManager(str(root)),run_limits=RunLimits(max_seconds=.3))
    agent._consolidator=None
    run,_=await execute(manager,session,agent)
    await asyncio.wait_for(agent.wait_for_memory_extractions(),2)
    calls=usage_summary(manager.database,run.id)['calls']
    assert len(calls)==2 and any(item['phase']=='memory_extraction' and item['status']=='failed' for item in calls)


@pytest.mark.asyncio
async def test_automatic_extraction_cannot_replace_manual_or_inactive_memory(workspace,monkeypatch):
    root,_,manager,session=workspace
    memory=MemoryManager(str(root));service=ProductService(manager,memory)
    service.update_memory('project','manual','Keep reviewed content',create=True,evidence='Reviewed by user')
    service.update_memory('project','inactive','Keep disabled content',create=True,enabled=False)
    class Client:
        async def stream(self,*args,**kwargs):
            yield TextDelta('\n---\n'.join('MEMORY_NAME: '+name+'\nMEMORY_TYPE: project\nMEMORY_DESC: automatic\nMEMORY_BODY: replacement' for name in ['manual','inactive','fresh']))
    conversation=ConversationManager();conversation.add_user_message('Remember this project fact')
    await memory.extract(Client(),conversation,'anthropic')
    assert 'Keep reviewed content' in (memory.project_mem_dir/'manual.md').read_text()
    assert 'Keep disabled content' in (memory.project_mem_dir/'inactive.md').read_text()
    assert 'replacement' in (memory.project_mem_dir/'fresh.md').read_text()


@pytest.mark.asyncio
async def test_workbench_rpc_privacy_and_busy_guard(workspace):
    from nonocode.remote import RemoteServer
    root,_,manager,session=workspace
    (root/'.nonocode/config.yaml').write_text('providers:\n- name: test\n  protocol: anthropic\n  base_url: https://example.com\n  model: test\n  api_key: private-test-secret\n')
    server=RemoteServer([]);server.session_manager=manager;server.memory_manager=MemoryManager(str(root));server.session_id=session.session_id
    server.agent=SimpleNamespace(work_dir=str(root))
    class Socket:
        async def send(self,payload):self.result=json.loads(payload)['data']
    socket=Socket()
    await server._handle_product_request(socket,{'request_id':'first','action':'settings'})
    assert socket.result['ok'] and 'private-test-secret' not in json.dumps(socket.result)
    server._streaming=True
    for action in ['settings_save','settings_test','memory_save','evolution_verify']:
        await server._handle_product_request(socket,{'request_id':action,'action':action})
        assert not socket.result['ok'] and '等待' in socket.result['error']
    await server._handle_product_request(socket,{'request_id':'read','action':'usage'})
    assert socket.result['ok']


def test_expected_internal_and_outside_writes_do_not_report_capture_errors(workspace):
    root,_,manager,session=workspace
    run=TrackedRun(manager.database,session.session_id,'go','test')
    store=ProductStore(manager.database)
    for path in [root/'.nonocode/internal.md',root.parent/'outside.md']:
        path.write_text('Internal bookkeeping')
        store.record_tool(run.id,'WriteFile',{'file_path':str(path)},ToolResult('written'))
        with pytest.raises(ValueError):store.capture_file(run.id,path)
    assert store.artifacts(run_id=run.id)==[]


@pytest.mark.asyncio
async def test_truncated_memory_extraction_is_not_saved_and_can_retry(workspace):
    root,_,_,_=workspace
    memory=MemoryManager(str(root));conversation=ConversationManager();conversation.add_user_message('Remember this durable project rule')
    text='MEMORY_NAME: durable-rule\nMEMORY_TYPE: project\nMEMORY_DESC: Persistent rule\nMEMORY_BODY: Complete content'
    class Client:
        def __init__(self,reason):self.reason=reason
        async def stream(self,*args,**kwargs):
            yield TextDelta(text);yield StreamEnd(self.reason,1,2)
    await memory.extract(Client('max_tokens'),conversation,'anthropic')
    assert not (memory.project_mem_dir/'durable-rule.md').exists()
    await memory.extract(Client('end_turn'),conversation,'anthropic')
    assert 'Complete content' in (memory.project_mem_dir/'durable-rule.md').read_text()
