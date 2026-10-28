import asyncio
import json
from types import SimpleNamespace

import pytest

from nonocode.agent import Agent
from nonocode.commands.handlers.evolve import handle_evolve
from nonocode.commands.registry import CommandContext
from nonocode.conversation import ConversationManager
from nonocode.evolution.store import EvolutionStore, observations
from nonocode.memory.auto_memory import MemoryManager
from nonocode.tools.base import TextDelta


def test_review_workflow_and_revoke(tmp_path):
    store = EvolutionStore(str(tmp_path))
    candidate = store.create("请用中文回复")
    assert store.guidance() == ""
    assert store.list() == [candidate]
    store.transition(candidate["id"], "apply")
    assert "请用中文回复" in store.guidance()
    with pytest.raises(ValueError):
        store.transition(candidate["id"], "apply")
    store.transition(candidate["id"], "revoke")
    assert store.guidance() == ""
    assert store.get(candidate["id"])["status"] == "revoked"


def test_rejected_and_invalid_candidates_never_apply(tmp_path):
    store = EvolutionStore(str(tmp_path))
    candidate = store.create("请先解释原因")
    store.transition(candidate["id"], "reject")
    with pytest.raises(ValueError):
        store.transition(candidate["id"], "apply")
    with pytest.raises(ValueError):
        store.get("../escape")
    with pytest.raises(ValueError):
        store.create(" ")
    assert store.guidance() == ""


def test_guidance_refreshes_for_existing_conversation(tmp_path):
    store = EvolutionStore(str(tmp_path))
    candidate = store.create("用中文回复")
    store.transition(candidate["id"], "apply")
    agent = Agent(client=None, registry=None, protocol="openai", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("hello")
    agent._sync_evolution_guidance(conv)
    assert sum("用中文回复" in message.content for message in conv.history) == 1
    agent._sync_evolution_guidance(conv)
    assert sum("用中文回复" in message.content for message in conv.history) == 1
    store.transition(candidate["id"], "revoke")
    agent._sync_evolution_guidance(conv)
    assert all("用中文回复" not in message.content for message in conv.history)
    assert conv.history[0].content == "hello"


def test_command_review_roundtrip(tmp_path):
    messages = []
    ctx = CommandContext(args="feedback 请先给结论", agent=SimpleNamespace(work_dir=str(tmp_path)),
                         conversation=None, session=None, session_manager=None, memory_manager=None,
                         ui=SimpleNamespace(add_system_message=messages.append), config={})
    asyncio.run(handle_evolve(ctx))
    item = EvolutionStore(str(tmp_path)).list()[0]
    assert item["status"] == "pending"
    ctx.args = f"show {item['id']}"
    asyncio.run(handle_evolve(ctx))
    assert "请先给结论" in messages[-1]
    ctx.args = f"apply {item['id']}"
    asyncio.run(handle_evolve(ctx))
    assert EvolutionStore(str(tmp_path)).get(item["id"])["status"] == "applied"


def test_reflection_requires_user_evidence_and_stays_pending(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    conversation = ConversationManager()
    conversation.inject_environment("injected environment preference")
    conversation.add_system_reminder("injected reminder preference")
    conversation.add_user_message("以后请先给结论")
    conversation.add_assistant_message("assistant-only preference")

    class Client:
        async def stream(self, conv, **kwargs):
            prompt = conv.history[0].content
            assert "injected reminder preference" not in prompt
            assert "injected environment preference" not in prompt
            yield TextDelta("\n---\n".join(
                f"EVOLUTION_FEEDBACK: {rule}\nEVOLUTION_EVIDENCE: {evidence}"
                for rule, evidence in (
                    ("先给结论，再解释原因", "以后请先给结论"),
                    ("invalid", "assistant-only preference"),
                    ("invalid", "injected reminder preference"),
                    ("invalid", "invented user feedback"),
                )
            ))

    asyncio.run(manager.extract(Client(), conversation, "openai"))
    store = EvolutionStore(str(tmp_path))
    [candidate] = store.list()
    assert candidate["source"] == "reflection"
    assert candidate["evidence"] == "以后请先给结论"
    assert candidate["status"] == "pending"
    assert store.guidance() == ""

    # A new process must not recreate a declined lesson, even if it is reworded.
    store.transition(candidate["id"], "reject")
    store.propose("先说结论", "以后请先给结论")
    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    asyncio.run(manager.extract(Client(), conversation, "openai"))
    assert len(store.list()) == 1
    assert store.get(candidate["id"])["status"] == "rejected"


def test_extraction_retains_messages_arriving_during_request_and_after_clear(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    conversation = ConversationManager()
    conversation.add_user_message("first request")
    prompts = []

    class Client:
        async def stream(self, conv, **kwargs):
            prompts.append(conv.history[0].content)
            if len(prompts) == 1:
                conversation.add_user_message("later request")
            yield TextDelta("NONE")

    async def run():
        await manager.extract(Client(), conversation, "openai")
        await manager.extract(Client(), conversation, "openai")
        fresh = ConversationManager()
        fresh.add_user_message("new session request")
        await manager.extract(Client(), fresh, "openai")

    asyncio.run(run())
    assert "later request" in prompts[1]
    assert "first request" not in prompts[1]
    assert "new session request" in prompts[2]


@pytest.mark.parametrize("field,value", [("status", []), ("created_at", {}), ("feedback", 42)])
def test_corrupt_records_do_not_break_assistant_guidance(tmp_path, field, value):
    store = EvolutionStore(str(tmp_path))
    item = store.create("good feedback")
    item[field] = value
    (store.directory / f"{item['id']}.json").write_text(json.dumps(item))
    assert store.list() == []
    assert store.guidance() == ""


def test_daily_evidence_accumulates_without_rewriting_reviewed_rule(tmp_path):
    store = EvolutionStore(str(tmp_path))
    item = store.propose("导出表格前先核对行数", "以后导出表格前检查行数", cue="导出表格时", signal="preference")
    store.transition(item["id"], "apply")
    for signal, quote in (("success", "检查行数后这次导出没有遗漏"),
                          ("failure", "虽然检查了行数，这次导出还是丢了一行")):
        item = EvolutionStore(str(tmp_path)).propose(
            "model attempted a different rule", quote, candidate_id=item["id"],
            cue="model attempted a different scope", signal=signal)
    assert item["feedback"] == "导出表格前先核对行数"
    assert item["cue"] == "导出表格时"
    assert item["status"] == "applied"
    assert len(observations(item)) == 3
    assert store.stats()["successes"] == 1
    assert store.stats()["failures"] == 1
    assert "导出表格时" in store.guidance()

    # Replays cannot inflate evidence or silently turn a failure into a success.
    again = store.propose(item["feedback"], "虽然检查了行数，这次导出还是丢了一行",
                          candidate_id=item["id"], signal="success")
    assert again == item
    store.transition(item["id"], "revoke")
    declined = store.propose(item["feedback"], "又一次反馈", candidate_id=item["id"])
    assert declined["status"] == "revoked"
    assert len(observations(declined)) == 3
    assert store.guidance() == ""


def test_daily_learning_links_paraphrases_across_conversations(tmp_path):
    from nonocode.memory.auto_memory import _extract_field

    first = ConversationManager()
    first.add_user_message("以后先说结论")

    class FirstClient:
        async def stream(self, conv, **kwargs):
            yield TextDelta("EVOLUTION_FEEDBACK: 先给结论，再解释\n"
                            "EVOLUTION_EVIDENCE: 以后先说结论\n"
                            "EVOLUTION_SIGNAL: preference\nEVOLUTION_CUE:\nEVOLUTION_TARGET:\n---\n")

    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    asyncio.run(manager.extract(FirstClient(), first, "openai"))
    store = EvolutionStore(str(tmp_path))
    [item] = store.list()
    second = ConversationManager()
    second.add_user_message("这次开头先给结论很清楚，继续保持")

    class SecondClient:
        async def stream(self, conv, **kwargs):
            assert item["id"] in conv.history[0].content
            yield TextDelta(f"EVOLUTION_FEEDBACK: 开头简明扼要\n"
                            f"EVOLUTION_EVIDENCE: 这次开头先给结论很清楚，继续保持\n"
                            f"EVOLUTION_SIGNAL: success\nEVOLUTION_CUE:\n"
                            f"EVOLUTION_TARGET: {item['id']}\n---\n")

    # A fresh manager represents the next invocation, with only persisted lessons.
    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    asyncio.run(manager.extract(SecondClient(), second, "openai"))
    [learned] = store.list()
    assert learned["feedback"] == item["feedback"]
    assert learned["status"] == "pending"
    assert len(observations(learned)) == 2
    assert observations(learned)[-1]["signal"] == "success"
    assert store.guidance() == ""
    assert _extract_field("EVOLUTION_CUE:\nEVOLUTION_TARGET: abc", "EVOLUTION_CUE") == ""


def test_legacy_record_can_accumulate_bounded_evidence(tmp_path):
    store = EvolutionStore(str(tmp_path))
    item = store.create("先核对再导出", source="reflection", evidence="原始反馈")
    del item["observations"]
    del item["cue"]
    (store.directory / f"{item['id']}.json").write_text(json.dumps(item))
    for i in range(25):
        item = store.propose("先核对再导出", f"第 {i} 次不同原话")
    assert len(observations(item)) == 20
    assert item["evidence"] == "原始反馈"
    assert observations(item)[-1]["quote"] == "第 24 次不同原话"
    assert store.stats()["observations"] == 20


@pytest.mark.parametrize("bad_history", [{}, [{"quote": "x", "signal": []}], [{"quote": 42, "signal": "success"}]])
def test_corrupt_observations_are_ignored(tmp_path, bad_history):
    store = EvolutionStore(str(tmp_path))
    item = store.create("a rule")
    item["observations"] = bad_history
    (store.directory / f"{item['id']}.json").write_text(json.dumps(item))
    assert store.list() == []
    assert store.stats()["observations"] == 0


def test_daily_status_and_show_expose_outcome_evidence(tmp_path):
    store = EvolutionStore(str(tmp_path))
    item = store.propose("先核对再导出", "核对后导出完整", cue="表格任务", signal="success")
    messages = []
    ctx = CommandContext(args="status", agent=SimpleNamespace(work_dir=str(tmp_path)),
                         conversation=None, session=None, session_manager=None, memory_manager=None,
                         ui=SimpleNamespace(add_system_message=messages.append), config={})
    asyncio.run(handle_evolve(ctx))
    assert "待审核 1" in messages[-1]
    assert "成功反馈 1 条" in messages[-1]
    ctx.args = f"show {item['id']}"
    asyncio.run(handle_evolve(ctx))
    assert "表格任务" in messages[-1]
    assert "[success] 核对后导出完整" in messages[-1]
