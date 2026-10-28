from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import uuid

from nonocode.agent import (AgentEvent, ErrorEvent, LoopComplete, PermissionRequest,
                            ToolResultEvent, ToolUseEvent, UsageEvent)
from nonocode.storage.database import Database


class TrackedRun:
    """Record every frontend's main execution without changing tool permissions."""

    def __init__(self, database: Database, session_id: str, prompt: str, source: str, *, input_offset: int = 0, output_offset: int = 0) -> None:
        self.database = database
        self.input_offset = input_offset
        self.output_offset = output_offset
        self.finished = False
        self.id = uuid.uuid4().hex
        self.database.create_run(self.id,session_id,prompt,source)

    async def events(self, events: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
        error = ''
        try:
            async for event in events:
                if isinstance(event,ToolUseEvent):
                    self.database.start_step(self.id,event.tool_id,event.tool_name,event.arguments)
                elif isinstance(event,ToolResultEvent):
                    self.database.finish_step(self.id,event.tool_id,event.output,event.is_error,event.elapsed)
                elif isinstance(event,PermissionRequest):
                    self.database.wait_for_permission(self.id,event.tool_name)
                elif isinstance(event,UsageEvent):
                    self.database.update_run(self.id,'running',input_tokens=max(0,event.input_tokens-self.input_offset),
                                             output_tokens=max(0,event.output_tokens-self.output_offset))
                elif isinstance(event,ErrorEvent):
                    error = event.message
                elif isinstance(event,LoopComplete):
                    self.database.update_run(self.id,'completed')
                    self.finished = True
                yield event
        except asyncio.CancelledError:
            self.database.update_run(self.id,'cancelled',error='用户取消了本次执行')
            self.finished = True
            raise
        except Exception as exc:
            self.database.update_run(self.id,'failed',error=str(exc))
            self.finished = True
            raise
        finally:
            if not self.finished:
                self.database.update_run(self.id,'failed' if error else 'interrupted',
                    error=error or '执行提前结束；可恢复会话，工具不会自动重放')
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

    def finish(self, status: str, error: str = '') -> None:
        if not self.finished:
            self.database.update_run(self.id,status,error=error)
            self.finished = True
