"""Root task accounting shared by agents, SDK attempts, retries and memory work."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import time
import uuid

from nonocode.limits import RunLimitError,RunLimits
from nonocode.storage.database import now

current_execution=ContextVar('nonocode_execution',default=None)
agent_depth=ContextVar('nonocode_agent_depth',default=0)
phase=ContextVar('nonocode_usage_phase',default='agent')


@dataclass
class ExecutionScope:
    database: object
    run_id: str
    root_id: str
    session_id: str
    limits: RunLimits | None = None
    started: float = 0
    counters: list | None = None

    def __post_init__(self):
        if self.limits and self.counters is None:self.counters=[0,0]

    def day(self):
        return datetime.now(ZoneInfo(getattr(self.limits,'budget_timezone','Asia/Shanghai'))).date().isoformat()

    def totals(self,*,daily=False):
        with self.database.connect() as connection:
            row=connection.execute('SELECT coalesce(sum(total_tokens),0),coalesce(sum(cost_usd),0),sum(cost_usd IS NULL) FROM usage_events WHERE '+('day_key=?' if daily else 'root_run_id=?'),
                (self.day() if daily else self.root_id,)).fetchone()
        return row[0],row[1],row[2] or 0

    def check(self, *, after=False):
        if not self.limits:return
        if self.counters is not None:
            for name,used in [('max_tool_calls',self.counters[0]),('max_iterations',self.counters[1])]:
                limit=getattr(self.limits,name)
                if limit and used>limit:raise RunLimitError('执行已停止：整次任务超过共享预算 '+name)
        total,cost,_=self.totals();daily,daily_cost,_=self.totals(daily=True)
        for name,value in [('max_tokens',total),('max_cost_usd',cost),('max_daily_tokens',daily),('max_daily_cost_usd',daily_cost)]:
            cap=getattr(self.limits,name,0)
            if cap and (value>cap if after else value>=cap):raise RunLimitError(f'执行已停止：共享预算 {name}={cap} 已用尽')
        if self.limits.max_seconds and time.monotonic()-self.started>self.limits.max_seconds:
            raise RunLimitError('执行已停止：整次任务超过共享时间预算')


def usage_summary(database,root_id=''):
    with database.connect() as connection:
        rows=connection.execute('''SELECT u.* FROM usage_events u WHERE u.workspace_id=? AND (?='' OR u.root_run_id=?) ORDER BY u.started_at DESC LIMIT 1000''',
                                (database.workspace_id,root_id,root_id)).fetchall()
        runs=connection.execute('SELECT id,root_run_id,parent_run_id,prompt,source,status,started_at,finished_at FROM runs WHERE workspace_id=? AND (?=\'\' OR root_run_id=? OR id=?) ORDER BY started_at DESC LIMIT 150',
                               (database.workspace_id,root_id,root_id,root_id)).fetchall()
        totals=connection.execute("SELECT coalesce(sum(total_tokens),0),coalesce(sum(cost_usd),0),coalesce(sum(cost_usd IS NULL),0) FROM usage_events WHERE workspace_id=? AND (?='' OR root_run_id=?)",(database.workspace_id,root_id,root_id)).fetchone()
    items=[dict(row) for row in rows]
    return {'calls':items,'runs':[dict(row) for row in runs], 'total_tokens':totals[0],
            'estimated_cost_usd':totals[1],
            'unknown_cost_calls':totals[2],'estimate_only':True,'limited':len(items)==1000}


async def metered_stream(client,original,*args,**kwargs):
    scope=current_execution.get()
    if scope is None:
        events=original(client,*args,**kwargs)
        try:
            async for event in events:yield event
        finally:
            close=getattr(events,'aclose',None)
            if close:await close()
        return
    scope.check()
    config=getattr(client,'_usage_config',None)
    pricing=getattr(config,'pricing',{}) or {}
    if scope.limits and (scope.limits.max_cost_usd or scope.limits.max_daily_cost_usd) and not pricing:
        raise RunLimitError('已设置金额预算，但当前模型没有价格配置；请先在配置中心填写价格')
    call_id=uuid.uuid4().hex;started=time.monotonic()
    with scope.database.connect() as connection:
        connection.execute('''INSERT INTO usage_events(id,workspace_id,run_id,root_run_id,phase,provider,model,status,pricing,started_at,day_key)
            VALUES(?,?,?,?,?,?,?,'running',?,?,?)''',(call_id,scope.database.workspace_id,scope.run_id,scope.root_id,phase.get(),
                getattr(config,'name',type(client).__name__),getattr(config,'model',getattr(client,'model','unknown')),json.dumps(pricing),now(),scope.day()))
    status='failed';events=original(client,*args,**kwargs)
    try:
        from nonocode.tools.base import StreamEnd
        remaining=max(0.001,scope.limits.max_seconds-(time.monotonic()-scope.started)) if scope.limits and scope.limits.max_seconds else None
        async with asyncio.timeout(remaining):
            async for event in events:
                if isinstance(event,StreamEnd):
                    values=[max(0,int(getattr(event,name,0) or 0)) for name in ['input_tokens','output_tokens','cache_read','cache_creation']]
                    incoming,outgoing,cached,creation=values
                    anthropic=getattr(config,'protocol','anthropic')=='anthropic'
                    uncached=incoming if anthropic else max(0,incoming-cached)
                    total=uncached+outgoing+cached+creation
                    cost=None
                    if pricing and total:
                        cost=(uncached*pricing.get('input_per_million',0)+outgoing*pricing.get('output_per_million',0)
                              +cached*pricing.get('cache_read_per_million',pricing.get('input_per_million',0))
                              +creation*pricing.get('cache_creation_per_million',pricing.get('input_per_million',0)))/1_000_000
                    with scope.database.connect() as connection:
                        connection.execute('''UPDATE usage_events SET status='completed',input_tokens=?,output_tokens=?,cache_read=?,cache_creation=?,total_tokens=?,cost_usd=?,finished_at=?,elapsed=? WHERE id=?''',
                                           (*values,total,cost,now(),time.monotonic()-started,call_id))
                    status='completed'
                    scope.check(after=True)
                yield event
    except TimeoutError:
        raise RunLimitError('模型调用超时，执行已停止') from None
    except asyncio.CancelledError:
        status='cancelled';raise
    finally:
        try:
            close=getattr(events,'aclose',None)
            if close:await close()
        finally:
            if status!='completed':
                with scope.database.connect() as connection:
                    connection.execute('UPDATE usage_events SET status=?,finished_at=?,elapsed=? WHERE id=?',(status,now(),time.monotonic()-started,call_id))


@asynccontextmanager
async def agent_scope(agent,prompt):
    scope=current_execution.get();depth=agent_depth.get()
    token_depth=agent_depth.set(depth+1);token_scope=None;child=None
    outcome={"error":""}
    try:
        if scope:
            if scope.limits is None:
                scope.limits=agent.run_limits;scope.started=time.monotonic();scope.counters=[0,0]
            scope.check()
            if depth:
                child=uuid.uuid4().hex
                scope.database.create_run(child,scope.session_id,prompt[:4000],'subagent')
                with scope.database.connect() as connection:
                    connection.execute('UPDATE runs SET root_run_id=?,parent_run_id=? WHERE id=?',(scope.root_id,scope.run_id,child))
                token_scope=current_execution.set(ExecutionScope(scope.database,child,scope.root_id,scope.session_id,scope.limits,scope.started,scope.counters))
        yield outcome
    except BaseException as exc:
        if child:scope.database.update_run(child,'cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',error=str(exc))
        raise
    else:
        if child:scope.database.update_run(child,'failed' if outcome['error'] else 'completed',error=outcome['error'])
    finally:
        if token_scope is not None:current_execution.reset(token_scope)
        agent_depth.reset(token_depth)


def usage_phase(label):
    def decorate(function):
        from functools import wraps
        @wraps(function)
        async def wrapped(*args,**kwargs):
            token=phase.set(label)
            try:return await function(*args,**kwargs)
            finally:phase.reset(token)
        return wrapped
    return decorate


async def record_tool(call,result,elapsed=0):
    scope=current_execution.get()
    if scope is None:return
    if agent_depth.get()>1:
        scope.database.finish_step(scope.run_id,call.tool_id,result.output,result.is_error,elapsed)
    from nonocode.storage.products import ProductStore
    try:
        await asyncio.to_thread(ProductStore(scope.database).record_tool,scope.run_id,call.tool_name,call.arguments,result)
        if call.tool_name=='ReadFile' and not result.is_error:
            from nonocode.memory.provenance import record_read
            await asyncio.to_thread(record_read,scope.database,scope.run_id,call.arguments.get('file_path',''))
    except (OSError,ValueError,KeyError):
        # Optional evidence capture cannot turn a successful tool action into a retry.
        import logging
        logging.getLogger(__name__).warning('任务证据暂未归档：%s',call.tool_name)


def iteration_started():
    scope=current_execution.get()
    if scope and scope.limits and scope.counters is not None:
        scope.counters[1]+=1
        if scope.limits.max_iterations and scope.counters[1]>scope.limits.max_iterations:
            raise RunLimitError('执行已停止：整次任务超过共享轮次预算')


def tool_started(call):
    scope=current_execution.get()
    if scope is None:return
    scope.check()
    if scope.limits and scope.counters is not None:
        scope.counters[0]+=1
        if scope.limits.max_tool_calls and scope.counters[0]>scope.limits.max_tool_calls:
            raise RunLimitError('执行已停止：整次任务超过共享工具调用预算')
    if agent_depth.get()>1:
        scope.database.start_step(scope.run_id,call.tool_id,call.tool_name,call.arguments)
