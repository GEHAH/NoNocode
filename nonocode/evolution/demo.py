"""Credential-free demonstration using real Agent file tools and artifact checks.

The model responses are scripted; this verifies the pipeline, not a real model gain.
Run: python -m nonocode.evolution.demo --output /tmp/nonocode-verified-demo
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from nonocode.evolution.store import EvolutionStore
from nonocode.evolution.verification import TaskStore, VerificationEngine, atomic_json, digest, runtime_context
from nonocode.tools.base import StreamEnd, TextDelta, ToolCallComplete

RULE = "When summing a signed ledger, include negative amounts as well as positive amounts."


class DemoClient:
    model = "scripted-local-demo"

    async def stream(self, conversation, system="", tools=None):
        calls = [call for message in conversation.history for call in message.tool_uses]
        if not calls:
            task = next(message.content for message in conversation.history if "\n\nInput files: " in message.content)
            name = task.split("\n\nInput files: ", 1)[1].splitlines()[0]
            yield ToolCallComplete("read-ledger", "ReadFile", {"file_path": name})
        elif not any(call.tool_name == "WriteFile" for call in calls):
            read = next(result.content for message in conversation.history for result in message.tool_results)
            raw = "\n".join(line.split("\t", 1)[1] for line in read.splitlines())
            amounts = [Decimal(row["amount"]) for row in csv.DictReader(io.StringIO(raw))]
            has_rule = any(RULE in message.content for message in conversation.history)
            total = sum((value for value in amounts if has_rule or value > 0), Decimal(0))
            yield ToolCallComplete("write-result", "WriteFile", {
                "file_path": "result.json", "content": json.dumps({"total": str(total)})})
        else:
            yield TextDelta("Wrote result.json.")
        yield StreamEnd("end_turn", input_tokens=20, output_tokens=10)


async def run_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    (output / "ledger.csv").write_text("amount\n100.25\n-20.10\n5.00\n", encoding="utf-8")
    spec = {"id": "signed-ledger", "prompt": "Sum all signed amounts in ledger.csv; write result.json with a string total.",
            "inputs": ["ledger.csv"], "checks": [
                {"type": "json_equals", "path": "result.json", "pointer": "/total", "value": "85.15"}],
            "repetitions": 2, "max_iterations": 5, "timeout_seconds": 10}
    atomic_json(output / "task.json", spec)
    TaskStore(str(output)).register("task.json")
    (output / "positive.csv").write_text("amount\n35.50\n", encoding="utf-8")
    regression = {**spec, "id": "positive-ledger", "inputs": ["positive.csv"],
                  "prompt": "Sum all signed amounts in positive.csv; write result.json with a string total.",
                  "checks": [{"type": "json_equals", "path": "result.json", "pointer": "/total", "value": "35.50"}]}
    atomic_json(output / "regression.json", regression)
    TaskStore(str(output)).register("regression.json")
    store = EvolutionStore(str(output))
    candidate = store.create(RULE, kind="capability", verification_policy="paired-v1", cue="Signed ledger calculations")
    parent = SimpleNamespace(client=DemoClient(), protocol="openai-compat", work_dir=str(output),
                             instructions_content="", context_window=128000)
    report = await VerificationEngine(parent, store).verify(candidate["id"], ["signed-ledger", "positive-ledger"])
    if report["outcome"] != "passed":
        raise RuntimeError(report["reason"])
    store.transition(candidate["id"], "apply", context_fingerprint=digest(runtime_context(parent, store)))
    assert RULE in store.guidance()
    store.transition(candidate["id"], "revoke")
    assert store.guidance() == ""
    assert (output / "ledger.csv").read_text() == "amount\n100.25\n-20.10\n5.00\n"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run_demo(args.output.expanduser().resolve()))
    print(f"Scripted demo: {report['outcome']}; {len(report['runs'])} real task executions; apply/revoke verified.")
    print(args.output.resolve() / ".nonocode" / "evolution" / "runs" / f"{report['id']}.json")


if __name__ == "__main__":
    main()
