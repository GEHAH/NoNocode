"""Non-interactive prompts load and persist memory before the process exits."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nonocode.__main__ import _run_prompt
from nonocode.config import ProviderConfig
from nonocode.permissions import PermissionMode
from nonocode.tools.base import StreamEnd, TextDelta


def test_prompt_loads_and_extracts_memory(tmp_path, monkeypatch, capsys):
    import nonocode.client
    from nonocode.memory.consolidation import MemoryConsolidator

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    memory_dir = tmp_path / ".nonocode" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("- [Preference](preference.md) — prefers concise answers\n")

    class FakeClient:
        saw_index = False

        async def stream(self, conversation, system="", tools=None):
            if system == "You are a memory extraction assistant.":
                # Yield control to verify that -p waits for background extraction.
                await asyncio.sleep(0.01)
                yield TextDelta("MEMORY_NAME: language\nMEMORY_TYPE: project\nMEMORY_DESC: preferred language\nMEMORY_BODY: Use Chinese\n---\n")
            else:
                self.saw_index = any("prefers concise answers" in str(m.content) for m in conversation.history)
                yield TextDelta("你好")
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)

    async def no_consolidation(*args, **kwargs):
        pass

    async def no_context_lookup(*args, **kwargs):
        pass

    client = FakeClient()
    monkeypatch.setattr(nonocode.client, "create_client", lambda provider: client)
    monkeypatch.setattr(nonocode.client, "resolve_context_window", no_context_lookup)
    monkeypatch.setattr(MemoryConsolidator, "maybe_run", no_consolidation)
    provider = ProviderConfig(name="test", protocol="openai-compat", model="test", api_key="test", base_url="https://example.com")
    config = SimpleNamespace(providers=[provider], worktree=None, enable_verification_agent=False,
                             enable_fork=False, enable_coordinator_mode=False)

    asyncio.run(_run_prompt(config, PermissionMode.DEFAULT, None, "请使用中文"))

    assert client.saw_index
    assert capsys.readouterr().out == "你好"
    assert "Use Chinese" in (memory_dir / "language.md").read_text()
    assert "language.md" in (memory_dir / "MEMORY.md").read_text()
