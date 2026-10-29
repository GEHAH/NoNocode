from __future__ import annotations
import asyncio
import json
from pathlib import Path

import pytest

from nonocode.agent import Agent
from nonocode.evolution.demo import DemoClient,RULE
from nonocode.evolution.lab import EvolutionLab
from nonocode.evolution.verification import TaskStore,VerificationEngine
from nonocode.memory.session import SessionManager
from nonocode.tools import ToolRegistry


@pytest.fixture
def lab(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'ledger.csv').write_text('amount\n100\n-10\n')
    (tmp_path/'holdout.csv').write_text('amount\n7\n-2\n')
    manager=SessionManager(str(tmp_path));session=manager.create()
    agent=Agent(DemoClient(),ToolRegistry(),'openai-compat',work_dir=str(tmp_path))
    service=EvolutionLab(agent,manager.database,session.session_id)
    for task_id,path,total,split in [('development','ledger.csv','90','development'),('holdout','holdout.csv','5','holdout')]:
        service.register_task({'id':task_id,'prompt':'Sum signed amounts and write result.json with a string total.',
            'inputs':[path],'checks':[{'type':'json_equals','path':'result.json','pointer':'/total','value':total}],
            'split':split,'repetitions':2,'timeout_seconds':5})
    yield service
    session.close()


@pytest.mark.asyncio
async def test_new_capability_requires_holdout_then_can_apply_and_revoke(lab):
    item=lab.create(RULE)
    assert item['verification_policy']=='holdout-v1'
    with pytest.raises(ValueError,match='holdout'):await lab.verify(item['id'],['development'])
    with pytest.raises(ValueError,match='执行验证'):lab.transition(item['id'],'apply')
    report=await lab.verify(item['id'],['development','holdout'])
    assert report['outcome']=='passed' and report['holdout_passed']
    assert len(report['runs'])==8 and all('elapsed' in trial for trial in report['runs'])
    assert report['tracking_run_id'] and lab.database.get_run(report['tracking_run_id'])['status']=='completed'
    assert lab.transition(item['id'],'apply')['status']=='applied'
    assert RULE in lab.store.guidance()
    lab.transition(item['id'],'revoke')
    assert RULE not in lab.store.guidance()


@pytest.mark.asyncio
async def test_changed_holdout_snapshot_invalidates_previous_pass(lab):
    item=lab.create(RULE)
    await lab.verify(item['id'],['development','holdout'])
    spec=lab.tasks.get('holdout')['spec'];spec['checks'][0]['value']='wrong'
    lab.register_task(spec)
    with pytest.raises(ValueError,match='更新'):lab.transition(item['id'],'apply')
    assert list((lab.tasks.directory/'history').glob('holdout-*.json'))


def test_preference_versions_restore_and_replacement_rollback(lab):
    original=lab.create('Use short answers',kind='preference')
    lab.transition(original['id'],'apply')
    replacement=lab.create('Use detailed answers',kind='preference',replaces_id=original['id'])
    lab.transition(replacement['id'],'apply')
    assert 'Use detailed answers' in lab.store.guidance() and 'Use short answers' not in lab.store.guidance()
    lab.transition(replacement['id'],'revoke')
    assert 'Use short answers' in lab.store.guidance() and 'Use detailed answers' not in lab.store.guidance()
    versions=lab.versions(original['id'])
    assert len(versions)>=2
    restored=lab.restore(original['id'],versions[-1]['id'])
    assert restored['status']=='pending' and restored['id']!=original['id']


def test_failed_task_draft_does_not_invent_success_criteria(lab):
    run_id='failed-run';lab.database.create_run(run_id,lab.session_id,'Create a report','test')
    lab.database.update_run(run_id,'failed',error='Output was incomplete')
    draft=lab.draft_from_run(run_id)
    assert draft['spec']['checks']==[] and draft['observed_failure']=='Output was incomplete'
    with pytest.raises(ValueError):lab.register_task(draft['spec'])


@pytest.mark.asyncio
async def test_cancelled_modern_recheck_cannot_reuse_previous_pass(lab,monkeypatch):
    item=lab.create(RULE)
    await lab.verify(item['id'],['development','holdout'])
    entered=asyncio.Event()
    async def waiting(*args,**kwargs):entered.set();await asyncio.sleep(60)
    monkeypatch.setattr(VerificationEngine,'_execute',waiting)
    task=asyncio.create_task(lab.verify(item['id'],['development','holdout']))
    await asyncio.wait_for(entered.wait(),3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    with pytest.raises(ValueError,match='执行验证'):lab.transition(item['id'],'apply')
    with lab.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM runs WHERE source='evolution' AND status='cancelled'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_lab_report_trace_and_artifact_preview_are_bounded(lab):
    item=lab.create(RULE)
    await lab.verify(item['id'],['development','holdout'])
    report=lab.report(item['id'])
    assert all('artifacts' not in trial and 'trace' not in trial for trial in report['runs'])
    trial=lab.trial(item['id'],0,'result.json')
    assert 'total' in trial['text'] and trial['trace']
    with pytest.raises(ValueError):lab.trial(item['id'],-1)
    with pytest.raises(ValueError):lab.trial(item['id'],0,'../private')
