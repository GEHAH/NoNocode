"""Review-oriented workbench over the existing deterministic verification engine."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import uuid

from nonocode.evolution.store import EvolutionStore
from nonocode.evolution.verification import TaskStore,TaskSpec,VerificationEngine,candidate_fingerprint,digest,runtime_context


class EvolutionLab:
    def __init__(self,agent,database=None,session_id=''):
        self.agent=agent;self.database=database;self.session_id=session_id
        self.store=EvolutionStore(agent.work_dir);self.tasks=TaskStore(agent.work_dir)

    def list(self):
        items=self.store.list();replaced={item.get('replaces_id') for item in items if item['status']=='applied'}
        return [{**item,'effective':item['status']=='applied' and item['id'] not in replaced} for item in items]

    def create(self,rule,kind='capability',cue='',evidence='',replaces_id=''):
        if replaces_id:self.store.get(replaces_id)
        item=self.store.create(rule,kind=kind,cue=cue,evidence=evidence)
        if replaces_id:
            item['replaces_id']=replaces_id;self.store._write(item)
        return item

    def versions(self,candidate_id):
        self.store.get(candidate_id)
        directory=self.store.directory/'versions'/candidate_id
        return sorted([json.loads(path.read_text()) for path in directory.glob('*.json')],key=lambda item:item['created_at'],reverse=True)

    def restore(self,candidate_id,version_id):
        versions=self.versions(candidate_id)
        previous=next((item['after'] for item in versions if item['id']==version_id),None)
        if previous is None:raise ValueError('历史版本不存在')
        current=self.store.get(candidate_id)
        return self.create(previous['feedback'],previous.get('kind','preference'),previous.get('cue',''),
                           evidence='用户恢复历史版本 '+version_id,replaces_id=current['id'] if current['status']=='applied' else previous.get('replaces_id',''))

    def transition(self,candidate_id,action):
        return self.store.transition(candidate_id,action,context_fingerprint=digest(runtime_context(self.agent,self.store)))

    def _report(self,candidate_id):
        item=self.store.get(candidate_id)
        if not item.get('verification'):raise ValueError('尚无验证报告')
        path=self.store.directory/'runs'/(item['verification']['run_id']+'.json')
        report=json.loads(path.read_text())
        if item['verification'].get('fingerprint')!=digest(report):raise ValueError('报告校验失败')
        return report

    def report(self,candidate_id):
        report=self._report(candidate_id)
        report['scope_note']='仅证明冻结测试任务上的结果；推广到其他任务仍需验证'
        for trial in report['runs']:
            trial['artifact_paths']=list(trial.pop('artifacts',{}))
            trial['trace_steps']=len(trial.pop('trace',[]))
        if self.database and report.get('tracking_run_id'):
            from nonocode.execution import usage_summary
            summary=usage_summary(self.database,report['tracking_run_id'])
            report['estimated_cost_usd']=summary['estimated_cost_usd']
            report['unknown_cost_calls']=summary['unknown_cost_calls']
        return report

    def trial(self,candidate_id,index,artifact_path=''):
        report=self._report(candidate_id)
        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(report['runs']):raise ValueError('无效试跑编号')
        trial=report['runs'][index]
        result={key:value for key,value in trial.items() if key!='artifacts'}
        result['artifact_paths']=list(trial.get('artifacts',{}))
        if artifact_path:
            if artifact_path not in trial.get('artifacts',{}):raise ValueError('此试跑未归档该产物')
            import base64
            result['text']=base64.b64decode(trial['artifacts'][artifact_path],validate=True).decode('utf-8',errors='replace')[:30000]
        return result

    def register_task(self,document):
        return self.tasks.register_document(document)

    def draft_from_run(self,run_id):
        if self.database is None:raise ValueError('执行记录不可用')
        run=self.database.get_run(run_id)
        if run is None:raise ValueError('任务不存在')
        failures=[step for step in run['steps'] if step['status'] in {'failed','interrupted'}]
        return {'source_run_id':run_id,'observed_failure':run['error'] or '\n'.join(step['output'][:2000] for step in failures),
                'spec':{'id':'case-'+uuid.uuid4().hex[:8],'prompt':run['prompt'][:8000], 'inputs':[],
                    'checks':[],'split':'development','repetitions':2,'timeout_seconds':60,'max_iterations':8},
                'note':'这是草稿：请补充输入与可执行成功标准后登记。失败输出不会自动成为期望答案。'}

    async def verify(self,candidate_id,task_ids,progress=None):
        from nonocode.execution import current_execution,ExecutionScope,agent_depth,phase
        import time
        run_id=None;token=None;depth=None;label=phase.set('evolution_verification')
        if self.database and current_execution.get() is None:
            run_id=uuid.uuid4().hex;self.database.create_run(run_id,self.session_id,'验证能力 '+candidate_id,'evolution')
            with self.database.connect() as connection:connection.execute('UPDATE runs SET root_run_id=id WHERE id=?',(run_id,))
            token=current_execution.set(ExecutionScope(self.database,run_id,run_id,self.session_id,self.agent.run_limits,time.monotonic()))
            depth=agent_depth.set(1)
        status='failed'
        try:
            report=await VerificationEngine(self.agent,self.store).verify(candidate_id,task_ids,progress)
            status='completed' if report['outcome'] in {'passed','inconclusive'} else 'failed'
            return report
        except asyncio.CancelledError:
            status='cancelled';raise
        finally:
            if run_id:self.database.update_run(run_id,status,error='' if status=='completed' else '能力验证未通过或提前终止')
            if token is not None:current_execution.reset(token)
            if depth is not None:agent_depth.reset(depth)
            phase.reset(label)
