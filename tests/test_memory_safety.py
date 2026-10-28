import asyncio

from nonocode.conversation import ConversationManager
from nonocode.memory.auto_memory import MemoryManager
from nonocode.memory.consolidation import MemoryConsolidator, _ConsolidationChecker
from nonocode.tools.base import TextDelta
from nonocode.tools.read_file import ReadFile
from nonocode.tools.write_file import WriteFile
from nonocode.tools.bash import Bash


def test_extraction_rejects_unsafe_names_and_symlinks(tmp_path, monkeypatch):
    monkeypatch.setenv("NONOCODE_REMOTE_MEMORY_DIR", str(tmp_path / "memory"))
    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    memory = tmp_path / "memory"
    memory.mkdir()
    secret = tmp_path / "outside.md"
    secret.write_text("untouched")
    (memory / "linked.md").symlink_to(secret)

    class Client:
        async def stream(self, *args, **kwargs):
            yield TextDelta("\n---\n".join(
                f"MEMORY_NAME: {name}\nMEMORY_TYPE: project\nMEMORY_DESC: d\nMEMORY_BODY: body"
                for name in ("../outside", "/tmp/escape", "linked", "good-name")
            ))

    conversation = ConversationManager()
    conversation.add_user_message("remember")
    asyncio.run(manager.extract(Client(), conversation, "openai"))
    assert secret.read_text() == "untouched"
    assert (memory / "good-name.md").is_file()
    assert not (tmp_path / "escape.md").exists()
    assert "good-name.md" in (memory / "MEMORY.md").read_text()
    assert "linked.md" not in (memory / "MEMORY.md").read_text()


def test_consolidation_checker_enforces_paths_and_tool_types(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (memory / "escape.md").symlink_to(outside)
    checker = _ConsolidationChecker(str(memory), "", str(sessions))
    def effect(tool, path):
        return checker.check(tool, {"file_path": str(path)}).effect
    assert effect(WriteFile(), memory / "note.md") == "allow"
    assert effect(ReadFile(), sessions / "one.jsonl") == "allow"
    assert effect(WriteFile(), sessions / "one.jsonl") == "deny"
    assert effect(WriteFile(), memory / "escape.md") == "deny"
    assert effect(WriteFile(), memory / ".." / "outside.md") == "deny"
    assert effect(ReadFile(), "relative.md") == "deny"
    assert effect(Bash(), memory / "note.md") == "deny"


def test_consolidator_uses_working_agent_api_and_restricted_registry(tmp_path, monkeypatch):
    import nonocode.agent
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, conversation):
            captured["prompt"] = conversation.history[0].content
            if False:
                yield None

    monkeypatch.setattr(nonocode.agent, "Agent", FakeAgent)
    consolidator = MemoryConsolidator(str(tmp_path))
    asyncio.run(consolidator._do_consolidation(None, None, "openai", []))
    registry = captured["registry"]
    assert registry.get("ReadFile") is not None
    assert registry.get("WriteFile") is not None
    assert registry.get("EditFile") is not None
    assert registry.get("Bash") is None
    assert captured["permission_checker"].check(WriteFile(), {"file_path": str(tmp_path / "code.py")}).effect == "deny"
    assert "only ReadFile, WriteFile, and EditFile" in captured["prompt"]
