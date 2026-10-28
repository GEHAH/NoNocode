from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from nonocode.client import LLMClient
from nonocode.config import AppConfig, ProviderConfig
from nonocode.permissions import PermissionMode
from nonocode.tools.base import StreamEnd, TextDelta


class ReplyClient(LLMClient):
    async def stream(self, conversation, system="", tools=None):
        yield TextDelta("Test reply")
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=2)


@pytest.fixture
def provider():
    return ProviderConfig(name="test", protocol="anthropic", model="test",
                          base_url="http://example.test", api_key="test-only",
                          context_window=128000)


@pytest.fixture
def remote(tmp_path, monkeypatch, provider):
    from nonocode.remote import RemoteServer
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("nonocode.remote.create_client", lambda config: ReplyClient())
    server = RemoteServer([provider], addr="127.0.0.1", port=0,
                          config=AppConfig([provider], permission_mode="acceptEdits"))
    server._init_agent()
    server.agent.memory_manager = None
    server.agent._consolidator = None
    server._broadcast = AsyncMock()
    yield server
    server.session.close()


@pytest.mark.asyncio
async def test_remote_conversation_is_persisted(remote):
    await remote._handle_user_message("Hello")
    await remote._handle_user_message("Second message")
    resumed = remote.session_manager.resume(remote.session_id)
    try:
        assert [(m.role, m.content) for m in resumed.messages] == [
            ("user", "Hello"), ("assistant", "Test reply"),
            ("user", "Second message"), ("assistant", "Test reply"),
        ]
        assert resumed.session.meta.total_tokens == 24
    finally:
        resumed.session.close()


@pytest.mark.asyncio
async def test_remote_session_commands_and_clear(remote):
    await remote._handle_user_message("Remember this conversation")
    old_id = remote.session_id
    old_history = remote.agent.file_history
    await remote._handle_slash_command("/session new")
    assert remote.session_id != old_id
    assert remote.agent.session_id == remote.session_id
    assert remote.agent.file_history is not old_history
    await remote._handle_slash_command(f"/session resume {old_id}")
    assert remote.session_id == old_id
    assert any(m.content == "Remember this conversation" for m in remote.conversation.history)
    await remote._handle_slash_command("/clear")
    assert remote.session_id != old_id
    assert remote.conversation.history == []
    assert remote.agent.total_input_tokens == 0
    await remote._handle_user_message("After clear")
    assert remote.conversation.history[-1].content == "Test reply"
    errors = [call.args[0] for call in remote._broadcast.call_args_list
              if call.args[0]["type"] == "error"]
    assert errors == []


@pytest.mark.asyncio
async def test_remote_skills_and_configured_permissions(remote):
    assert remote.agent.permission_mode == PermissionMode.ACCEPT_EDITS
    await remote._handle_slash_command("/skill list")
    await asyncio.sleep(0)
    messages = [call.args[0] for call in remote._broadcast.call_args_list]
    assert not any("未初始化" in str(message) for message in messages)
    assert remote._build_command_context("").config["skill_loader"] is remote.skill_loader
    await remote._handle_slash_command("/plan")
    assert remote.agent.permission_mode == PermissionMode.PLAN


@pytest.mark.asyncio
async def test_tui_new_resume_and_local_commands(tmp_path, monkeypatch, provider):
    from nonocode.app import NoNocodeApp
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("nonocode.app.create_client", lambda config: ReplyClient())
    app = NoNocodeApp(providers=[provider], enable_fork=False)
    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.pause()
        app.agent.memory_manager = None
        app.agent._consolidator = None
        errors = []
        monkeypatch.setattr(app, "_show_error", errors.append)
        await app._send_message("Original conversation")
        old_id = app.session.session_id
        resumed = app.session_manager.resume(old_id)
        try:
            assert [(m.role, m.content) for m in resumed.messages] == [
                ("user", "Original conversation"), ("assistant", "Test reply"),
            ]
        finally:
            resumed.session.close()
        old_history = app.agent.file_history
        app.agent.active_skills["old-skill"] = "old skill body"
        for command in ("/help", "/status", "/session list", "/memory list",
                        "/evolve status", "/permission", "/mcp", "/skill list", "/sandbox"):
            await app._dispatch_command(command)
        await app._dispatch_command("/session new")
        assert app.session.session_id != old_id
        assert app.agent.file_history is not old_history
        assert app.agent.active_skills == {}
        assert app.agent.total_input_tokens == 0
        await app._dispatch_command("/session resume")
        candidates = app.session_manager.list()[:15]
        number = next(i for i, meta in enumerate(candidates, 1) if meta.id == old_id)
        await app._dispatch_command(f"/session resume {number}")
        assert app.session.session_id == old_id
        assert any(m.content == "Original conversation" for m in app.conversation.history)
        await app._dispatch_command("/clear")
        assert app.session.session_id != old_id
        assert app.conversation.history == []
        await app._send_message("After clear")
        assert any(m.content == "Test reply" for m in app.conversation.history)
        assert errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", ["direct", "interactive", "noninteractive"])
@pytest.mark.parametrize("reject", [False, True])
async def test_tool_hooks_on_all_execution_paths(execution, reject, tmp_path):
    from types import SimpleNamespace
    from nonocode.agent import Agent, PermissionRequest, PermissionResponse
    from nonocode.hooks import HookEngine
    from nonocode.hooks.models import Action, Hook
    from nonocode.tools import create_default_registry
    from nonocode.tools.base import ToolCallComplete, ToolResult

    registry = create_default_registry()
    tool = registry.get("Bash")
    tool.execute = AsyncMock(return_value=ToolResult(output="executed"))
    engine = HookEngine([
        Hook(id="pre", event="pre_tool_use", action=Action(type="prompt", message="pre"), reject=reject),
        Hook(id="post", event="post_tool_use", action=Action(type="prompt", message="post")),
    ])
    checker = None
    if execution == "interactive":
        checker = SimpleNamespace(mode=PermissionMode.DEFAULT,
                                  check=lambda *args: SimpleNamespace(effect="ask", reason="confirm"))
    agent = Agent(ReplyClient(), registry, "anthropic", work_dir=str(tmp_path),
                  hook_engine=engine, permission_checker=checker)
    call = ToolCallComplete("call", "Bash", {"command": "printf harmless"})
    if execution == "direct":
        result = (await agent._execute_single_tool_direct(call)).result
    elif execution == "noninteractive":
        result = await agent._execute_tool_noninteractive(call)
    else:
        async for event in agent._execute_tool(call):
            if isinstance(event, PermissionRequest):
                assert not reject
                event.future.set_result(PermissionResponse.ALLOW)
            else:
                result, _ = event
    assert result.is_error is reject
    if reject:
        tool.execute.assert_not_awaited()
    else:
        tool.execute.assert_awaited_once()
    assert [n.hook_id for n in engine.drain_notifications()] == (["pre"] if reject else ["pre", "post"])


@pytest.mark.asyncio
async def test_remote_http_websocket_chat_and_cancel(remote):
    import json
    from types import MethodType
    import httpx
    import websockets
    from nonocode.remote import RemoteServer

    remote._broadcast = MethodType(RemoteServer._broadcast, remote)

    async def receive_until(ws, kind):
        received = []
        async with asyncio.timeout(5):
            while True:
                message = json.loads(await ws.recv())
                received.append(message)
                if message['type'] == kind:
                    return received

    async with websockets.serve(remote._ws_handler, '127.0.0.1', 0,
                                process_request=remote._process_http_request) as listener:
        port = listener.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(trust_env=False) as client:
            page = await client.get(f'http://127.0.0.1:{port}/')
            assert page.status_code == 200 and 'NoNocode' in page.text
            assert (await client.get(f'http://127.0.0.1:{port}/missing')).status_code == 404
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws') as ws:
            initial = await receive_until(ws, 'commands')
            assert initial[0]['type'] == 'connected'
            await ws.send(json.dumps({'type': 'ping'}))
            await receive_until(ws, 'pong')
            for prompt in ('Hello over WebSocket', 'Another message'):
                await ws.send(json.dumps({'type': 'user_message', 'data': {'content': prompt}}))
                messages = await receive_until(ws, 'loop_complete')
                assert any(m['type'] == 'stream_text' for m in messages)
                assert not any(m['type'] == 'error' for m in messages)
            await ws.send(json.dumps({'type': 'user_message', 'data': {'content': '/clear'}}))
            await receive_until(ws, 'command_done')
            assert remote.conversation.history == []

            started = asyncio.Event()
            class SlowClient(LLMClient):
                async def stream(self, conversation, system='', tools=None):
                    started.set()
                    await asyncio.Event().wait()
                    yield TextDelta('unreachable')

            remote.agent.client = SlowClient()
            await ws.send(json.dumps({'type': 'user_message', 'data': {'content': 'Slow response'}}))
            await asyncio.wait_for(started.wait(), 5)
            await ws.send(json.dumps({'type': 'cancel'}))
            messages = await receive_until(ws, 'error')
            assert messages[-1]['data']['message'] == 'Operation cancelled'
            assert not remote._streaming
            assert remote._pending_perms == {}


@pytest.mark.asyncio
async def test_real_stdio_mcp_connection_and_status(remote, tmp_path):
    import sys
    from nonocode.config import MCPServerConfig
    script = tmp_path / 'echo_server.py'
    script.write_text('''from mcp.server.fastmcp import FastMCP
server = FastMCP("audit")
@server.tool()
def echo(text: str) -> str:
    return "echo:" + text
if __name__ == "__main__":
    server.run(transport="stdio")
''')
    remote._mcp_server_configs = [MCPServerConfig(
        name='audit', command=sys.executable, args=[str(script)],
    )]
    async with asyncio.timeout(20):
        try:
            await remote._init_mcp()
            tool = remote.registry.get('mcp_audit_echo')
            assert tool is not None
            result = await tool.execute(tool.params_model(text='hello'))
            assert not result.is_error and 'echo:hello' in result.output
            assert 'mcp_audit_echo' in remote._mcp_instructions
            await remote._handle_slash_command('/mcp')
            await asyncio.sleep(0)
            messages = [call.args[0] for call in remote._broadcast.call_args_list]
            assert any('audit: 1 tools' in str(m) for m in messages)
        finally:
            if remote.mcp_manager:
                await remote.mcp_manager.shutdown()


@pytest.mark.asyncio
async def test_remote_manual_compact_survives_resume(remote):
    from nonocode.agent import CompactNotification
    from nonocode.context import CompactBoundary
    from nonocode.conversation import Message
    await remote._handle_user_message('Before compaction')
    remote.agent.total_input_tokens = 6000
    keep = [Message(role='user', content='Retained message')]
    remote.agent.manual_compact = AsyncMock(return_value=CompactNotification(
        before_tokens=1000, message='Compacted', boundary=CompactBoundary(summary='Audit summary', keep=keep),
    ))
    await remote._handle_slash_command('/compact')
    resumed = remote.session_manager.resume(remote.session_id)
    try:
        assert any('Audit summary' in message.content for message in resumed.messages)
        assert any(message.content == 'Retained message' for message in resumed.messages)
        assert not any(message.content == 'Before compaction' for message in resumed.messages)
    finally:
        resumed.session.close()
