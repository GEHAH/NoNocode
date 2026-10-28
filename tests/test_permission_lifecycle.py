"""Permission prompts must be removed before tool execution can continue."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from nonocode.agent import PermissionRequest, PermissionResponse, ToolUseEvent
from nonocode.app import ChatInput, NoNocodeApp, ToolCallBlock
from nonocode.client import LLMClient
from nonocode.config import ProviderConfig
from nonocode.permission_dialog import InlinePermissionWidget
from nonocode.tools.base import StreamEnd, TextDelta, ToolCallComplete


class FileClient(LLMClient):
    def __init__(self, paths=(), text_after_tools=False):
        self.paths = paths
        self.calls = 0
        self.text_after_tools = text_after_tools

    async def stream(self, conversation, system='', tools=None):
        self.calls += 1
        if self.calls == 1 and self.paths:
            for index, path in enumerate(self.paths):
                yield ToolCallComplete(f'read-{index}', 'ReadFile', {'file_path': str(path)})
            if self.text_after_tools:
                yield TextDelta('Reading the project files.')
            yield StreamEnd('tool_use', input_tokens=10, output_tokens=2)
        else:
            yield TextDelta('Project files read successfully.')
            yield StreamEnd('end_turn', input_tokens=10, output_tokens=2)


@asynccontextmanager
async def running_app(tmp_path, monkeypatch, client):
    work = tmp_path / 'workspace'
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr('nonocode.app.create_client', lambda config: client)
    provider = ProviderConfig(name='test', protocol='anthropic', model='test',
                              base_url='http://example.test', api_key='test', context_window=128000)
    app = NoNocodeApp(providers=[provider], enable_fork=False)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.pause()
        app.agent.memory_manager = None
        app.agent._consolidator = None
        monkeypatch.setattr(app, '_prefetch_relevant_memories', AsyncMock(return_value=''))
        monkeypatch.setattr(app, '_update_session_summary', AsyncMock())
        errors = []
        monkeypatch.setattr(app, '_show_error', errors.append)
        yield app, pilot, errors


async def wait_for_prompt(app, task, path):
    async with asyncio.timeout(5):
        while True:
            if task.done():
                await task
                raise AssertionError('Agent finished before the permission prompt')
            widgets = list(app.query(InlinePermissionWidget))
            if widgets and widgets[0]._description == str(path):
                return widgets[0]
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_permission_reply_waits_for_widget_removal(tmp_path, monkeypatch):
    async with running_app(tmp_path, monkeypatch, FileClient()) as (app, pilot, errors):
        future = asyncio.get_running_loop().create_future()
        request = PermissionRequest('ReadFile', '/outside/README.md', future)
        await app._handle_permission_request(request)
        widget = app.query_one(InlinePermissionWidget)
        remove_started, release_remove = asyncio.Event(), asyncio.Event()
        remove = widget.remove

        async def delayed_remove():
            remove_started.set()
            await release_remove.wait()
            await remove()

        monkeypatch.setattr(widget, 'remove', lambda: asyncio.create_task(delayed_remove()))
        widget.action_select()
        try:
            await asyncio.wait_for(remove_started.wait(), 5)
            assert not future.done(), 'Tool resumed while old permission widget still exists'
        finally:
            release_remove.set()
            await pilot.pause()
        assert await future == PermissionResponse.ALLOW
        assert not list(app.query(InlinePermissionWidget))
        assert not app.query_one(ChatInput).disabled
        assert errors == []


@pytest.mark.asyncio
async def test_read_two_external_project_files_with_sequential_permissions(tmp_path, monkeypatch):
    paths = [tmp_path / 'README.md', tmp_path / 'pyproject.toml']
    for path in paths:
        path.write_text('content of ' + path.name)
    async with running_app(tmp_path, monkeypatch, FileClient(paths)) as (app, pilot, errors):
        monkeypatch.setattr(app.agent.permission_checker.sandbox, 'check',
                            lambda path: (False, 'outside project'))
        task = asyncio.create_task(app._send_message('Introduce this project'))
        app._agent_task = task
        try:
            for path in paths:
                widget = await wait_for_prompt(app, task, path)
                widget.action_select()
                widget.action_select()  # Rapid duplicate Enter must not approve a later prompt.
                await pilot.pause()
            await asyncio.wait_for(task, 5)
            blocks = list(app.query(ToolCallBlock))
            assert len(blocks) == 2
            assert all(not block._loading and not block._is_error for block in blocks)
            assert 'pyproject.toml' in blocks[-1]._full_output
            assert app.conversation.history[-1].content == 'Project files read successfully.'
            assert errors == []
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_permission_cleans_prompt_input_and_tool_spinner(tmp_path, monkeypatch):
    path = tmp_path / 'pyproject.toml'
    path.write_text('[project]\nname="test"')
    async with running_app(tmp_path, monkeypatch, FileClient([path])) as (app, pilot, errors):
        monkeypatch.setattr(app.agent.permission_checker.sandbox, 'check',
                            lambda path: (False, 'outside project'))
        task = asyncio.create_task(app._send_message('Read project'))
        app._agent_task = task
        widget = await wait_for_prompt(app, task, path)
        request = app._pending_perm_request
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await pilot.pause()
        assert not list(app.query(InlinePermissionWidget))
        assert request.future.done()
        assert not app.query_one(ChatInput).disabled
        assert all(not block._loading for block in app.query(ToolCallBlock))
        assert not app._streaming
        # Events from the removed prompt must not complete a future request.
        widget.action_select()
        await pilot.pause()
        await app._send_message('Continue')
        assert app.conversation.history[-1].content == 'Project files read successfully.'
        assert errors == []


@pytest.mark.asyncio
async def test_text_after_tool_block_does_not_abort_stream(tmp_path, monkeypatch):
    async with running_app(tmp_path, monkeypatch, FileClient(text_after_tools=True)) as (app, pilot, errors):
        path = tmp_path / 'workspace' / 'pyproject.toml'
        path.write_text('[project]\nname="test"')
        app.agent.client.paths = [path]
        await app._send_message('Read and explain')
        assert all(not block._loading for block in app.query(ToolCallBlock))
        assert app.conversation.history[-1].content == 'Project files read successfully.'
        assert errors == []


@pytest.mark.asyncio
async def test_unexpected_error_is_visible_and_finishes_tool_spinner(tmp_path, monkeypatch):
    async with running_app(tmp_path, monkeypatch, FileClient()) as (app, pilot, errors):
        async def failed_run(conversation):
            yield ToolUseEvent('ReadFile', 'read', {'file_path': 'pyproject.toml'})
            raise RuntimeError('simulated tool pipeline failure')
        monkeypatch.setattr(app.agent, 'run', failed_run)
        await app._send_message('Read project')
        assert any('simulated tool pipeline failure' in error for error in errors)
        assert all(not block._loading and block._is_error for block in app.query(ToolCallBlock))
        assert not app._streaming


@pytest.mark.asyncio
async def test_database_write_failure_restores_input_and_streaming_state(tmp_path, monkeypatch):
    import sqlite3
    async with running_app(tmp_path, monkeypatch, FileClient()) as (app,pilot,errors):
        def failed_write(*args):
            raise sqlite3.OperationalError('simulated disk failure')
        monkeypatch.setattr(app.session_manager.database,'append_records',failed_write)
        await app._send_message('Read project')
        await pilot.pause()
        assert not app._streaming
        assert not app.query_one(ChatInput).disabled
        assert not list(app.query(InlinePermissionWidget))
        assert any('simulated disk failure' in error for error in errors)
