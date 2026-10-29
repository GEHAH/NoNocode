import asyncio
import json
import os
from pathlib import Path
import shlex
import signal
import sys

import pytest
from pydantic import BaseModel

from nonocode.agent import Agent, ErrorEvent, LoopComplete, StreamText, StreamingExecutor, ToolResultEvent
from nonocode.conversation import ConversationManager
from nonocode.limits import RunLimits, RunLimitError
from nonocode.tools import ToolRegistry
from nonocode.tools.base import Tool, ToolResult, ToolCallComplete, StreamEnd, TextDelta
from nonocode.tools.bash import Bash, Params
from test_agent import MockLLMClient


class Empty(BaseModel):
    pass


class WaitingTool(Tool):
    name = 'Wait'
    description = 'Wait until cancelled'
    category = 'read'
    is_concurrency_safe = True
    params_model = Empty

    def __init__(self):
        self.started, self.stopped = asyncio.Event(), asyncio.Event()

    async def execute(self, params):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.stopped.set()


def conversation():
    conv = ConversationManager()
    conv.add_user_message('go')
    return conv


async def until_exists(path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'posix', reason='POSIX process group cleanup')
@pytest.mark.parametrize('stop', ['cancel','timeout'])
async def test_bash_stops_descendants_even_if_the_shell_exits(tmp_path, stop):
    marker, later = tmp_path/'ready', tmp_path/'should-not-exist'
    child = ('import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
             f'pathlib.Path({str(marker)!r}).write_text("ready");time.sleep(1.6);'
             f'pathlib.Path({str(later)!r}).write_text("orphan survived")')
    # Background child inherits stdout; shell can already have exited when cancelled.
    command = f'{shlex.quote(sys.executable)} -c {shlex.quote(child)} &'
    task = asyncio.create_task(Bash().execute(Params(command=command,timeout=1)))
    await until_exists(marker)
    if stop == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    else:
        result = await asyncio.wait_for(task, 3)
        assert result.is_error and 'timed out' in result.output
    await asyncio.sleep(1.2 if stop == 'cancel' else .15)
    assert not later.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('command,error', [
    ('exit 7',True),('printf "ok"',False),('printf x | grep missing',False),
    ('grep x /definitely-nonexistent-nonocode-file',True),('test 1 = 2',False),
    ('false && grep x',True),('find /definitely-nonexistent-nonocode-path',True),
])
async def test_real_bash_exit_status(command,error):
    result = await Bash().execute(Params(command=command))
    assert result.is_error is error


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stream_error','cancel','close','deadline'])
async def test_streaming_tool_is_reaped_on_all_exits(tmp_path, failure):
    tool = WaitingTool()
    registry = ToolRegistry()
    registry.register(tool)
    stream_closed = asyncio.Event()
    class Client:
        async def stream(self,*args,**kwargs):
            try:
                yield ToolCallComplete('wait-1','Wait',{})
                await tool.started.wait()
                if failure == 'stream_error':
                    raise RuntimeError('model stream disconnected')
                await asyncio.Future()
            finally:
                stream_closed.set()
    agent = Agent(Client(),registry,'anthropic',work_dir=str(tmp_path),
                  run_limits=RunLimits(max_seconds=.15 if failure=='deadline' else 3))
    events = agent.run(conversation())
    if failure == 'close':
        await anext(events)
        await tool.started.wait()
        await events.aclose()
    else:
        async def consume():
            return [event async for event in events]
        task = asyncio.create_task(consume())
        await tool.started.wait()
        if failure == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif failure == 'stream_error':
            with pytest.raises(RuntimeError,match='disconnected'):
                await task
        else:
            results = await task
            assert any(isinstance(event,ErrorEvent) and 'max_seconds' in event.message for event in results)
            assert not any(isinstance(event,LoopComplete) for event in results)
    assert tool.stopped.is_set()
    assert stream_closed.is_set()


@pytest.mark.asyncio
async def test_executor_preserves_failed_tool_identity():
    executor = StreamingExecutor()
    async def fail():
        raise ValueError('bad')
    executor.submit(fail(),'id-7','Example')
    [result] = await executor.collect_results()
    assert (result.tool_id,result.tool_name,result.result.is_error) == ('id-7','Example',True)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['stream','batch'])
@pytest.mark.parametrize('limit',['max_tool_calls','max_repeated_calls','max_iterations','max_tokens'])
async def test_limits_stop_real_attempts_without_false_completion(tmp_path, mode, limit):
    executed = []
    class CountingTool(Tool):
        name='Count'; description='count'; params_model=Empty
        async def execute(self,params):
            executed.append(1)
            return ToolResult('ok')
    registry = ToolRegistry(); registry.register(CountingTool())
    calls = [[ToolCallComplete(str(i),'Count',{}),StreamEnd('tool_use',10,2)] for i in range(5)]
    client = MockLLMClient(calls)
    limits = RunLimits(max_repeated_calls=0,**{limit:1}) if limit!='max_repeated_calls' else RunLimits(max_repeated_calls=1)
    agent = Agent(client,registry,'anthropic',work_dir=str(tmp_path),run_limits=limits)
    if mode == 'stream':
        events = [event async for event in agent.run(conversation())]
        assert any(isinstance(event,ErrorEvent) for event in events)
        assert not any(isinstance(event,LoopComplete) for event in events)
    else:
        with pytest.raises(RunLimitError):
            await agent.run_to_completion('go')
    assert len(executed) <= (0 if limit == 'max_tokens' else 1)


@pytest.mark.asyncio
async def test_token_truncation_does_not_replay_completed_tool(tmp_path):
    class Count(Tool):
        name='Count';description='count';params_model=Empty
        calls=0
        async def execute(self,params):
            self.calls+=1
            return ToolResult('done')
    tool=Count();registry=ToolRegistry();registry.register(tool)
    client=MockLLMClient([[ToolCallComplete('once','Count',{}),StreamEnd('max_tokens',1,1)],
                         [TextDelta('Done'),StreamEnd('end_turn',1,1)]])
    agent=Agent(client,registry,'anthropic',work_dir=str(tmp_path))
    events=[event async for event in agent.run(conversation())]
    assert tool.calls==1
    assert len([event for event in events if isinstance(event,ToolResultEvent)])==1
    assert any(isinstance(event,LoopComplete) for event in events)


@pytest.mark.asyncio
async def test_write_then_read_keeps_order(tmp_path):
    order=[]
    class Write(Tool):
        name='Write';description='write';params_model=Empty;category='write'
        async def execute(self,params):
            await asyncio.sleep(.02);order.append('written');return ToolResult('ok')
    class Read(Write):
        name='Read';category='read';is_concurrency_safe=True
        async def execute(self,params):
            assert order == ['written'];order.append('read');return ToolResult('ok')
    registry=ToolRegistry();registry.register(Write());registry.register(Read())
    client=MockLLMClient([[ToolCallComplete('w','Write',{}),ToolCallComplete('r','Read',{}),StreamEnd('tool_use',1,1)],
                          [TextDelta('done'),StreamEnd('end_turn',1,1)]])
    events=[event async for event in Agent(client,registry,'anthropic',work_dir=str(tmp_path)).run(conversation())]
    assert order==['written','read']
    assert not any(isinstance(e,ToolResultEvent) and e.is_error for e in events)


@pytest.mark.asyncio
async def test_file_read_does_not_block_loop_and_enforces_size(tmp_path):
    from nonocode.tools.read_file import ReadFile, Params as ReadParams
    source=tmp_path/'source.txt';source.write_text('first\nsecond\nthird\n')
    result=await ReadFile().execute(ReadParams(file_path=str(source),offset=1,limit=1))
    assert result.output=='2\tsecond' and not result.is_error
    source.write_bytes(b'x'*4_000_001)
    result=await ReadFile().execute(ReadParams(file_path=str(source)))
    assert result.is_error and '4 MB' in result.output


@pytest.mark.asyncio
async def test_terminal_error_cannot_be_overwritten_by_completion(tmp_path):
    from nonocode.memory.session import SessionManager
    from nonocode.storage.runs import TrackedRun
    manager=SessionManager(str(tmp_path));session=manager.create()
    run=TrackedRun(manager.database,session.session_id,'go','test')
    async def events():
        yield ErrorEvent('real failure')
        yield LoopComplete(1)
    async for _ in run.events(events()):pass
    record=manager.database.get_run(run.id)
    assert record['status']=='failed' and record['error']=='real failure'
    session.close()


@pytest.mark.asyncio
async def test_cancelled_filesystem_worker_is_killed(tmp_path,monkeypatch):
    from nonocode.tools.isolated import filesystem_worker
    original=asyncio.create_subprocess_exec
    processes=[]
    async def spawn(*args,**kwargs):
        proc=await original(sys.executable,'-c','import time;time.sleep(30)',**kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    task=asyncio.create_task(filesystem_worker('_file_reader.py','unused','0','1'))
    async with asyncio.timeout(3):
        while not processes:await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_storage_failure_still_closes_running_events():
    from nonocode.storage.runs import TrackedRun
    from nonocode.agent import ToolUseEvent
    closed=asyncio.Event()
    class BrokenDatabase:
        def create_run(self,*args):pass
        def start_step(self,*args):raise OSError('disk full')
        def update_run(self,*args,**kwargs):raise OSError('disk full')
    async def events():
        try:
            yield ToolUseEvent('Wait','t1',{})
            await asyncio.Future()
        finally:closed.set()
    run=TrackedRun(BrokenDatabase(),'session','go','test')
    with pytest.raises(OSError,match='disk full'):
        async for _ in run.events(events()):pass
    assert closed.is_set()


@pytest.mark.asyncio
async def test_inner_stream_cancellation_is_reported_without_waiting_for_deadline(tmp_path):
    class Client:
        async def stream(self,*args,**kwargs):
            raise asyncio.CancelledError()
            yield
    agent=Agent(Client(),ToolRegistry(),'anthropic',work_dir=str(tmp_path))
    async def consume():return [event async for event in agent.run(conversation())]
    events=await asyncio.wait_for(consume(),1)
    assert len(events)==1 and isinstance(events[0],ErrorEvent) and '提前取消' in events[0].message


@pytest.mark.asyncio
async def test_cancelled_child_tool_keeps_identity():
    executor=StreamingExecutor()
    async def cancelled():raise asyncio.CancelledError()
    executor.submit(cancelled(),'cancelled-id','Tool')
    [result]=await executor.collect_results()
    assert result.tool_id=='cancelled-id' and result.result.is_error
