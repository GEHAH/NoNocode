"""验证 /clear 命令正确重置所有会话级状态。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nonocode.agent import Agent, ErrorEvent, StreamText
from nonocode.client import LLMClient
from nonocode.commands.registry import CommandContext
from nonocode.commands.handlers.clear import handle_clear
from nonocode.conversation import ConversationManager
from nonocode.memory.session import SessionManager
from nonocode.filehistory import FileHistory
from nonocode.tools import create_default_registry
from nonocode.tools.base import StreamEnd, TextDelta


class MockUI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    def refresh_status(self) -> None:
        pass


def make_agent(work_dir: str) -> Agent:
    agent = Agent(
        client=MagicMock(spec=LLMClient),
        registry=create_default_registry(),
        protocol="anthropic",
        work_dir=work_dir,
    )
    agent._loop_count = 5
    agent.total_input_tokens = 12345
    agent.total_output_tokens = 6789
    agent.session_id = "old-session"
    agent.active_skills = {"test": "body"}
    return agent


@pytest.mark.asyncio
async def test_clear_resets_session_id(tmp_path) -> None:
    tmpdir = str(tmp_path)
    sm = SessionManager(tmpdir)
    old_session = sm.create()
    old_id = old_session.session_id

    agent = make_agent(tmpdir)
    agent.session_id = old_id
    old_history = FileHistory(tmpdir, old_id)
    agent.file_history = old_history
    history_tools = [tool for tool in agent.registry.list_tools() if hasattr(tool, "file_history")]
    for tool in history_tools:
        tool.file_history = old_history
    ui = MockUI()

    new_session_holder: list = []
    new_conversation_holder: list = []
    old_conversation = ConversationManager()
    old_conversation.add_user_message("Previous conversation")

    def set_session(s):
        new_session_holder.append(s)
        agent.session_id = s.session_id

    ctx = CommandContext(
        args="",
        agent=agent,
        conversation=old_conversation,
        session=old_session,
        session_manager=sm,
        memory_manager=None,
        ui=ui,
        config={
            "set_session": set_session,
            "set_conversation": new_conversation_holder.append,
            "clear_chat": lambda: None,
        },
    )

    await handle_clear(ctx)

    assert len(new_session_holder) == 1, "应创建新 session"
    assert new_session_holder[0].session_id != old_id, "新 session ID 应不同"
    assert agent.session_id == new_session_holder[0].session_id
    assert agent.file_history is not old_history
    assert (tmp_path / ".nonocode" / "file-history" / agent.session_id).is_dir()
    assert history_tools
    assert all(tool.file_history is agent.file_history for tool in history_tools)
    assert len(new_conversation_holder) == 1
    conversation = new_conversation_holder[0]
    assert conversation is not old_conversation
    assert conversation.history == []
    assert ui.messages == ["对话已清除，新会话已创建"]

    async def stream(*args, **kwargs):
        yield TextDelta("Ready for the new conversation.")
        yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)

    agent.client.stream = stream
    conversation.add_user_message("Hello again")
    events = [event async for event in agent.run(conversation)]
    assert not any(isinstance(event, ErrorEvent) for event in events)
    assert "".join(event.text for event in events if isinstance(event, StreamText)) == "Ready for the new conversation."


@pytest.mark.asyncio
async def test_clear_resets_token_counts(tmp_path) -> None:
    tmpdir = str(tmp_path)
    sm = SessionManager(tmpdir)
    session = sm.create()
    agent = make_agent(tmpdir)

    ctx = CommandContext(
        args="",
        agent=agent,
        conversation=ConversationManager(),
        session=session,
        session_manager=sm,
        memory_manager=None,
        ui=MockUI(),
        config={
            "set_session": lambda s: None,
            "set_conversation": lambda c: None,
            "clear_chat": lambda: None,
        },
    )

    await handle_clear(ctx)

    assert agent.total_input_tokens == 0, "input tokens 应归零"
    assert agent.total_output_tokens == 0, "output tokens 应归零"


@pytest.mark.asyncio
async def test_clear_resets_loop_count_and_skills(tmp_path) -> None:
    tmpdir = str(tmp_path)
    sm = SessionManager(tmpdir)
    session = sm.create()
    agent = make_agent(tmpdir)
    agent.active_skills = {"skill1": "body1"}

    ctx = CommandContext(
        args="",
        agent=agent,
        conversation=ConversationManager(),
        session=session,
        session_manager=sm,
        memory_manager=None,
        ui=MockUI(),
        config={
            "set_session": lambda s: None,
            "set_conversation": lambda c: None,
            "clear_chat": lambda: None,
        },
    )

    await handle_clear(ctx)

    assert agent._loop_count == 0, "loop count 应归零"
    assert len(agent.active_skills) == 0, "active skills 应清空"
