"""Per-invocation execution limits, shared by the streaming and batch loops."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import json
import time


class RunLimitError(RuntimeError):
    pass


@dataclass
class RunLimits:
    max_seconds: int = 900
    max_iterations: int = 50
    max_tool_calls: int = 100
    max_tokens: int = 200000
    max_repeated_calls: int = 4
    max_daily_tokens: int = 1000000
    max_cost_usd: float = 0
    max_daily_cost_usd: float = 0
    budget_timezone: str = "Asia/Shanghai"


class RunBudget:
    def __init__(self, limits: RunLimits):
        self.limits = limits
        self.started = time.monotonic()
        self.tool_calls = 0
        self.last_call = None
        self.repeated = 0
        self.tokens = 0

    def check(self, iteration: int = 0):
        for name, used, limit in [
            ('max_seconds', time.monotonic() - self.started, self.limits.max_seconds),
            ('max_iterations', iteration, self.limits.max_iterations),
            ('max_tokens', self.tokens, self.limits.max_tokens),
        ]:
            if limit and used > limit:
                raise RunLimitError(f'执行已停止：达到 run_limits.{name}={limit}')

    def claim_tool(self, call):
        self.check()
        signature = (call.tool_name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
        repeated = self.repeated + 1 if signature == self.last_call else 1
        if self.limits.max_tool_calls and self.tool_calls >= self.limits.max_tool_calls:
            raise RunLimitError(f'执行已停止：达到 run_limits.max_tool_calls={self.limits.max_tool_calls}')
        if self.limits.max_repeated_calls and repeated > self.limits.max_repeated_calls:
            raise RunLimitError(f'执行已停止：相同工具与参数连续调用超过 {self.limits.max_repeated_calls} 次')
        self.tool_calls += 1
        self.last_call, self.repeated = signature, repeated


current_budget: ContextVar[RunBudget | None] = ContextVar('nonocode_run_budget', default=None)


async def bounded_events(factory, limits):
    """Keep the deadline inside a producer task, never cancel a UI consumer."""
    budget = RunBudget(limits)
    queue = asyncio.Queue(maxsize=64)

    async def produce():
        token = current_budget.set(budget)
        events = factory()
        try:
            try:
                try:
                    async for event in events:
                        await queue.put(('event', event))
                finally:
                    await events.aclose()
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                await queue.put(('error', RunLimitError('模型或工具提前取消，执行已停止')))
            except BaseException as exc:
                await queue.put(('error', exc))
            else:
                await queue.put(('done', None))
        finally:
            current_budget.reset(token)

    task = asyncio.create_task(produce())
    try:
        while True:
            timeout = max(0.001, limits.max_seconds - (time.monotonic() - budget.started)) if limits.max_seconds else None
            try:
                async with asyncio.timeout(timeout):
                    kind, value = await queue.get()
            except asyncio.TimeoutError:
                raise RunLimitError(f'执行已停止：达到 run_limits.max_seconds={limits.max_seconds}') from None
            if kind == 'done':
                break
            if kind == 'error':
                raise value
            yield value
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
