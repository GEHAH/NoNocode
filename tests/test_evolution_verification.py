import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nonocode.evolution.demo import DemoClient, RULE, run_demo
from nonocode.evolution.store import EvolutionStore
from nonocode.evolution.verification import (
    ArtifactCheck, TaskStore, VerificationEngine, WorkspaceFileTool, atomic_json, check_artifacts,
    digest, runtime_context,
)
from nonocode.tools.base import StreamEnd, TextDelta, ToolCallComplete
from nonocode.tools.read_file import Params as ReadParams, ReadFile
from nonocode.tools.write_file import Params as WriteParams, WriteFile


def setup_task(tmp_path, *, checks=None):
    (tmp_path / "ledger.csv").write_text("amount\n100.25\n-20.10\n5.00\n")
    spec = {"id": "ledger", "prompt": "Sum all signed amounts in ledger.csv and write result.json with a string total.",
            "inputs": ["ledger.csv"], "checks": checks or [
                {"type": "json_equals", "path": "result.json", "pointer": "/total", "value": "85.15"}],
            "timeout_seconds": 2, "max_iterations": 5}
    atomic_json(tmp_path / "task.json", spec)
    TaskStore(str(tmp_path)).register("task.json")
    store = EvolutionStore(str(tmp_path))
    item = store.create(RULE, kind="capability")
    parent = SimpleNamespace(client=DemoClient(), protocol="openai-compat", work_dir=str(tmp_path),
                             instructions_content="", context_window=128000)
    return store, item, parent


def test_real_file_executions_validate_and_allow_adoption_and_revoke(tmp_path):
    store, item, parent = setup_task(tmp_path)
    with pytest.raises(ValueError, match="执行验证"):
        store.transition(item["id"], "apply")
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "passed"
    assert len(report["runs"]) == 4
    assert [run["arm"] for run in report["runs"]] == ["baseline", "candidate", "candidate", "baseline"]
    assert all(run["trace"] and run["artifacts"] for run in report["runs"])
    assert all(run["checks"][0]["passed"] == (run["arm"] == "candidate") for run in report["runs"])
    assert "result.json" not in {path.name for path in tmp_path.iterdir()}
    store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))
    assert RULE in store.guidance()
    store.transition(item["id"], "revoke")
    assert RULE not in store.guidance()
    assert (store.directory / "runs" / f"{report['id']}.json").is_file()


@pytest.mark.parametrize("change", ["candidate", "model", "instructions", "task", "active_rules"])
def test_stale_evidence_cannot_enable_a_capability(tmp_path, change):
    store, item, parent = setup_task(tmp_path)
    asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    if change == "candidate":
        current = store.get(item["id"])
        current["feedback"] += " changed"
        store._write(current)
    elif change == "model":
        parent.client = SimpleNamespace(model="different-model")
    elif change == "instructions":
        parent.instructions_content = "changed instructions"
    elif change == "task":
        spec = json.loads((tmp_path / "task.json").read_text())
        spec["checks"][0]["value"] = "999"
        atomic_json(tmp_path / "task.json", spec)
        TaskStore(str(tmp_path)).register("task.json")
    else:
        preference = store.create("different active rule")
        store.transition(preference["id"], "apply")
    with pytest.raises(ValueError, match="执行验证"):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


class AlwaysRight(DemoClient):
    async def stream(self, conversation, **kwargs):
        if not any(message.tool_uses for message in conversation.history):
            yield ToolCallComplete("write", "WriteFile", {"file_path": "result.json", "content": '{"total":"85.15"}'})
        else:
            yield TextDelta("done")
        yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


class AlwaysWrong(AlwaysRight):
    async def stream(self, conversation, **kwargs):
        async for event in super().stream(conversation, **kwargs):
            if isinstance(event, ToolCallComplete):
                event.arguments["content"] = '{"total":"wrong"}'
            yield event


class SelfReportedSuccess(AlwaysRight):
    async def stream(self, conversation, **kwargs):
        yield TextDelta("All checks passed! I am verified.")
        yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)


@pytest.mark.parametrize("client,outcome", [(AlwaysRight, "inconclusive"), (AlwaysWrong, "failed"),
                                           (SelfReportedSuccess, "failed")])
def test_no_improvement_and_self_reports_never_pass(tmp_path, client, outcome):
    store, item, parent = setup_task(tmp_path)
    parent.client = client()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == outcome
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_regression_on_another_task_blocks_adoption(tmp_path):
    store, item, parent = setup_task(tmp_path)
    spec = json.loads((tmp_path / "task.json").read_text())
    spec["id"] = "regression"
    spec["checks"][0]["value"] = "105.25"
    atomic_json(tmp_path / "second.json", spec)
    TaskStore(str(tmp_path)).register("second.json")
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger", "regression"]))
    assert report["outcome"] == "failed"
    assert len(report["runs"]) == 8


def test_reports_need_all_trials_and_the_frozen_checks(tmp_path):
    store, item, parent = setup_task(tmp_path)
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    path = store.directory / "runs" / f"{report['id']}.json"
    original = path.read_text()
    report["runs"].pop()
    atomic_json(path, report)
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))
    report = json.loads(original)
    for run in report["runs"]:
        run["checks"][0]["check"]["value"] = "forged"
    atomic_json(path, report)
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_probe_file_tools_cannot_escape_or_edit_the_grader(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("untouched")
    (workspace / "link").symlink_to(secret)
    read = WorkspaceFileTool(ReadFile(), workspace)
    write = WorkspaceFileTool(WriteFile(), workspace)
    for name in (str(secret), "../secret.txt", "link", ".nonocode/evolution/fake.json"):
        assert asyncio.run(read.execute(ReadParams(file_path=name))).is_error
        assert asyncio.run(write.execute(WriteParams(file_path=name, content="changed"))).is_error
    assert secret.read_text() == "untouched"
    assert not (workspace / ".nonocode").exists()


@pytest.mark.parametrize("bad_inputs", [["../secret"], [".env"], [".nonocode/config.yaml"]])
def test_task_registration_rejects_unsafe_inputs(tmp_path, bad_inputs):
    setup_task(tmp_path)
    spec = json.loads((tmp_path / "task.json").read_text())
    spec["inputs"] = bad_inputs
    atomic_json(tmp_path / "bad.json", spec)
    with pytest.raises(ValueError):
        TaskStore(str(tmp_path)).register("bad.json")


def test_task_uses_frozen_inputs_and_hides_oracles_from_solver(tmp_path):
    store, item, parent = setup_task(tmp_path)
    (tmp_path / "ledger.csv").write_text("amount\n999\n")

    class InspectingClient(DemoClient):
        async def stream(self, conversation, **kwargs):
            first_request = not any(message.tool_uses for message in conversation.history)
            if first_request:
                content = "\n".join(message.content for message in conversation.history)
                assert "85.15" not in content
                assert {tool["name"] for tool in kwargs["tools"]} == {"ReadFile", "WriteFile", "EditFile"}
            async for event in super().stream(conversation, **kwargs):
                yield event

    parent.client = InspectingClient()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "passed"
    assert (tmp_path / "ledger.csv").read_text() == "amount\n999\n"


def test_promoting_an_active_preference_requires_verification(tmp_path):
    store = EvolutionStore(str(tmp_path))
    item = store.create("old procedure")
    store.transition(item["id"], "apply")
    promoted = store.promote(item["id"])
    assert promoted["kind"] == "capability" and promoted["status"] == "pending"
    assert store.guidance() == ""
    with pytest.raises(ValueError, match="执行验证"):
        store.transition(item["id"], "apply")


def test_scripted_demo_executes_and_reverts(tmp_path):
    report = asyncio.run(run_demo(tmp_path))
    assert report["outcome"] == "passed"
    assert len(report["runs"]) == 8
    assert EvolutionStore(str(tmp_path)).get(report["candidate_id"])["status"] == "revoked"


def test_failed_recheck_replaces_previous_passing_evidence(tmp_path):
    store, item, parent = setup_task(tmp_path)
    old = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    parent.client = AlwaysWrong()
    latest = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert latest["outcome"] == "failed"
    assert store.get(item["id"])["verification"]["run_id"] == latest["id"]
    assert (store.directory / "runs" / f"{old['id']}.json").exists()
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_infrastructure_error_is_not_counted_as_a_baseline_failure(tmp_path):
    store, item, parent = setup_task(tmp_path)

    class BrokenBaseline(DemoClient):
        async def stream(self, conversation, **kwargs):
            if not any(RULE in message.content for message in conversation.history):
                raise RuntimeError("service unavailable")
            async for event in super().stream(conversation, **kwargs):
                yield event

    parent.client = BrokenBaseline()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "error"
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_one_fortunate_comparison_is_inconclusive(tmp_path):
    store, item, parent = setup_task(tmp_path)

    class UnstableBaseline(DemoClient):
        baseline_writes = 0

        async def stream(self, conversation, **kwargs):
            candidate = any(RULE in message.content for message in conversation.history)
            async for event in super().stream(conversation, **kwargs):
                if isinstance(event, ToolCallComplete) and event.tool_name == "WriteFile" and not candidate:
                    self.baseline_writes += 1
                    if self.baseline_writes == 2:
                        event.arguments["content"] = '{"total":"85.15"}'
                yield event

    parent.client = UnstableBaseline()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "inconclusive"


def test_iteration_limit_cannot_produce_a_passing_run(tmp_path):
    store, item, parent = setup_task(tmp_path)

    class NeverFinishes(DemoClient):
        async def stream(self, conversation, **kwargs):
            yield ToolCallComplete("again", "ReadFile", {"file_path": "ledger.csv"})
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)

    parent.client = NeverFinishes()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "error"
    assert all("maximum iterations" in run["error"] for run in report["runs"])


def test_commands_register_verify_report_apply_and_revoke(tmp_path):
    from nonocode.commands.handlers.evolve import handle_evolve
    from nonocode.commands.registry import CommandContext

    store, item, parent = setup_task(tmp_path)
    messages = []
    ctx = CommandContext(args="tasks", agent=parent, conversation=None, session=None,
                         session_manager=None, memory_manager=None,
                         ui=SimpleNamespace(add_system_message=messages.append), config={})
    async def run():
        for command in ("task task.json", "tasks", f"verify {item['id']} ledger",
                        f"report {item['id']}", f"apply {item['id']}"):
            ctx.args = command
            await handle_evolve(ctx)
        assert store.get(item["id"])["status"] == "applied"
        ctx.args = f"revoke {item['id']}"
        await handle_evolve(ctx)

    asyncio.run(run())
    assert store.get(item["id"])["status"] == "revoked"
    assert any("执行验证：passed" in message for message in messages)
    assert not any("操作失败" in message for message in messages)


def test_daily_procedure_proposal_needs_execution_before_adoption(tmp_path):
    from nonocode.conversation import ConversationManager
    from nonocode.memory.auto_memory import MemoryManager

    class Reflector:
        async def stream(self, *args, **kwargs):
            yield TextDelta("EVOLUTION_FEEDBACK: Sum negative amounts too\n"
                            "EVOLUTION_EVIDENCE: 负数也要计入总和\nEVOLUTION_KIND: capability\n"
                            "EVOLUTION_SIGNAL: correction\nEVOLUTION_CUE: Ledger tasks\n---\n")

    manager = MemoryManager(str(tmp_path))
    manager._user_mem_dir = ""
    conversation = ConversationManager()
    conversation.add_user_message("负数也要计入总和")
    asyncio.run(manager.extract(Reflector(), conversation, "openai"))
    store = EvolutionStore(str(tmp_path))
    [item] = store.list()
    assert item["kind"] == "capability"
    with pytest.raises(ValueError, match="执行验证"):
        store.transition(item["id"], "apply")


def test_cancelled_recheck_invalidates_previous_pass(tmp_path):
    store, item, parent = setup_task(tmp_path)
    asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))

    async def recheck():
        started = asyncio.Event()

        class HangingClient(DemoClient):
            async def stream(self, *args, **kwargs):
                started.set()
                await asyncio.Future()
                if False:
                    yield None

        parent.client = HangingClient()
        task = asyncio.create_task(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(recheck())
    assert "verification" not in store.get(item["id"])
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_changed_artifact_evidence_invalidates_report(tmp_path):
    store, item, parent = setup_task(tmp_path)
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    report["runs"][1]["artifacts"]["result.json"] = "e30="
    atomic_json(store.directory / "runs" / f"{report['id']}.json", report)
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))


def test_fixed_checkers_read_actual_text_csv_and_json_artifacts(tmp_path):
    (tmp_path / "report.md").write_text("Summary: complete")
    (tmp_path / "data.csv").write_text("name\none\ntwo\n")
    (tmp_path / "data.json").write_text('{"items":[{"a/b":true}]}')
    checks = [ArtifactCheck(type="file_exists", path="report.md"),
              ArtifactCheck(type="text_contains", path="report.md", text="Summary:"),
              ArtifactCheck(type="csv_rows", path="data.csv", rows=2),
              ArtifactCheck(type="json_equals", path="data.json", pointer="/items/0/a~1b", value=True)]
    results, artifacts = check_artifacts(tmp_path, checks)
    assert all(row["passed"] for row in results)
    assert set(artifacts) == {"report.md", "data.csv", "data.json"}
    (tmp_path / "data.csv").write_text("name\none\n")
    results, _ = check_artifacts(tmp_path, checks)
    assert results[2]["passed"] is False


@pytest.mark.parametrize("mutation", ["only_exists", "one_trial", "arbitrary_command"])
def test_invalid_validation_contracts_are_rejected(tmp_path, mutation):
    setup_task(tmp_path)
    spec = json.loads((tmp_path / "task.json").read_text())
    if mutation == "only_exists":
        spec["checks"] = [{"type": "file_exists", "path": "result.json"}]
    elif mutation == "one_trial":
        spec["repetitions"] = 1
    else:
        spec["checks"] = [{"type": "command", "path": "result.json", "command": "echo fake"}]
    atomic_json(tmp_path / "bad.json", spec)
    with pytest.raises(ValueError):
        TaskStore(str(tmp_path)).register("bad.json")


def test_timeout_stops_further_model_calls_and_blocks_adoption(tmp_path):
    store, item, parent = setup_task(tmp_path)
    spec = json.loads((tmp_path / "task.json").read_text())
    spec["timeout_seconds"] = 1
    atomic_json(tmp_path / "task.json", spec)
    TaskStore(str(tmp_path)).register("task.json")

    class SlowClient(DemoClient):
        calls = 0

        async def stream(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(30)
            yield StreamEnd("end_turn", input_tokens=1, output_tokens=1)

    parent.client = SlowClient()
    report = asyncio.run(VerificationEngine(parent, store).verify(item["id"], ["ledger"]))
    assert report["outcome"] == "error"
    assert len(report["runs"]) == 1
    assert "超时" in report["runs"][0]["error"]
    assert parent.client.calls == 1
    with pytest.raises(ValueError):
        store.transition(item["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))
