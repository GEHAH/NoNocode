"""Paired task executions with frozen inputs and deterministic artifact checks.

The solver can only read and write files in a temporary workspace. It receives
neither the expected answers nor the evaluator's state, and cannot run commands.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nonocode.permissions import Decision, PermissionMode
from nonocode.tools.base import Tool, ToolResult

ENGINE_VERSION = 2
MAX_FILE_BYTES = 1_000_000
MAX_INPUT_BYTES = 8_000_000
_TASK_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_BLOCKED = {".git", ".nonocode", ".codex", ".agents", ".aws", ".venv", "__pycache__"}


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or path.is_absolute() or ":" in value
            or ".." in path.parts or not path.parts
            or any(part in _BLOCKED or part.startswith(".env") for part in path.parts)):
        raise ValueError(f"验证文件路径不允许：{value}")
    return str(path)


def workspace_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        try:
            value = str(path.relative_to(root))
        except ValueError as exc:
            raise ValueError("试跑只能访问自己的工作区") from exc
    relative = relative_path(value)
    path = root / relative
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValueError("验证不允许符号链接")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("验证路径越界")
    return path


class ArtifactCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["file_exists", "text_contains", "json_equals", "csv_rows"]
    path: str
    text: str = ""
    pointer: str = ""
    value: Any = None
    rows: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_fields(self):
        relative_path(self.path)
        if self.type == "text_contains" and not self.text:
            raise ValueError("text_contains 需要非空 text")
        if self.type == "json_equals" and "value" not in self.model_fields_set:
            raise ValueError("json_equals 需要 value（可以是 null）")
        if self.pointer and (self.type != "json_equals" or not self.pointer.startswith("/")):
            raise ValueError("pointer 须为 JSON Pointer，例如 /total")
        return self


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    prompt: str = Field(min_length=1, max_length=8000)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    checks: list[ArtifactCheck] = Field(min_length=1, max_length=30)
    repetitions: int = Field(default=2, ge=2, le=3)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_iterations: int = Field(default=8, ge=1, le=20)
    split: Literal["development", "holdout"] = "development"

    @model_validator(mode="after")
    def check_inputs(self):
        self.inputs = [relative_path(path) for path in self.inputs]
        if len(self.inputs) != len(set(self.inputs)):
            raise ValueError("inputs 不允许重复")
        if all(check.type == "file_exists" for check in self.checks):
            raise ValueError("至少需要一个内容检查，不能仅检查文件存在")
        return self


class TaskStore:
    def __init__(self, work_dir: str):
        self.root = Path(work_dir).resolve()
        self.directory = self.root / ".nonocode" / "evolution" / "tasks"

    def _path(self, task_id: str) -> Path:
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("验证任务 ID 无效")
        return self.directory / f"{task_id}.json"

    def register(self, spec_path: str) -> dict:
        source = workspace_path(self.root, spec_path)
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("任务定义过大")
        spec = TaskSpec.model_validate_json(source.read_text(encoding="utf-8"))
        return self.register_document(spec.model_dump())

    def register_document(self, document: dict) -> dict:
        spec = TaskSpec.model_validate(document)
        files = {}
        total = 0
        for name in spec.inputs:
            path = workspace_path(self.root, name)
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"输入须为不超过 1 MB 的普通文件：{name}")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_INPUT_BYTES:
                raise ValueError("验证输入总量超过 8 MB")
            files[name] = base64.b64encode(data).decode("ascii")
        payload = {"spec": spec.model_dump(), "files": files}
        record = {**payload, "fingerprint": digest(payload), "created_at": datetime.now(timezone.utc).isoformat()}
        destination = self._path(spec.id)
        if destination.exists():
            previous = self.get(spec.id)
            atomic_json(self.directory / 'history' / (spec.id+'-'+previous['fingerprint']+'.json'), previous)
        atomic_json(destination, record)
        return record

    def get(self, task_id: str) -> dict:
        try:
            record = json.loads(self._path(task_id).read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
                raise ValueError("任务快照结构无效")
            spec = TaskSpec.model_validate(record["spec"])
            if spec.id != task_id or set(record["files"]) != set(spec.inputs):
                raise ValueError("任务输入不匹配")
            total = 0
            for name, encoded in record["files"].items():
                relative_path(name)
                data = base64.b64decode(encoded, validate=True)
                total += len(data)
                if len(data) > MAX_FILE_BYTES or total > MAX_INPUT_BYTES:
                    raise ValueError("任务输入过大")
            if record["fingerprint"] != digest({"spec": record["spec"], "files": record["files"]}):
                raise ValueError("任务快照摘要不匹配")
            return record
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"验证任务 {task_id} 无效或不存在：{exc}") from exc

    def list(self) -> list[dict]:
        records = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                records.append(self.get(path.stem))
            except ValueError:
                continue
        return records


class WorkspaceFileTool(Tool):
    """Resolve every file path before invoking the real file tool; no cwd mutation."""
    def __init__(self, tool: Tool, root: Path):
        self.tool, self.root = tool, root
        self.name, self.description = tool.name, tool.description
        self.params_model, self.category = tool.params_model, tool.category

    async def execute(self, params):
        try:
            path = workspace_path(self.root, params.file_path)
            if self.category == "write" and len(getattr(params, "content", "").encode()) > MAX_FILE_BYTES:
                raise ValueError("试跑输出文件超过 1 MB")
            if path.exists() and path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("试跑文件超过 1 MB")
            if self.name == "EditFile" and path.is_file():
                changed = path.read_text(encoding="utf-8").replace(params.old_string, params.new_string, 1)
                if len(changed.encode()) > MAX_FILE_BYTES:
                    raise ValueError("试跑输出文件超过 1 MB")
            return await self.tool.execute(params.model_copy(update={"file_path": str(path)}))
        except (ValueError, OSError) as exc:
            return ToolResult(output=f"Probe denied: {exc}", is_error=True)


class ProbeChecker:
    mode = PermissionMode.ACCEPT_EDITS

    def check(self, tool, arguments):
        return Decision("allow" if isinstance(tool, WorkspaceFileTool) else "deny", "Probe file tools only")


def check_artifacts(workspace: Path, checks: list[ArtifactCheck]) -> tuple[list[dict], dict]:
    results, artifacts = [], {}
    for check in checks:
        passed, detail = False, ""
        try:
            path = workspace_path(workspace, check.path)
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("产物缺失或超过 1 MB")
            data = path.read_bytes()
            artifacts[check.path] = base64.b64encode(data).decode("ascii")
            if check.type == "file_exists":
                passed = True
            elif check.type == "text_contains":
                passed = check.text in data.decode("utf-8")
            elif check.type == "csv_rows":
                rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
                passed = len(rows) == check.rows
            else:
                value = json.loads(data.decode("utf-8"))
                if check.pointer:
                    for key in check.pointer[1:].split("/"):
                        key = key.replace("~1", "/").replace("~0", "~")
                        value = value[int(key)] if isinstance(value, list) else value[key]
                passed = digest(value) == digest(check.value)
            detail = "通过" if passed else "内容不符合固定检查"
        except (OSError, ValueError, TypeError, KeyError, IndexError, csv.Error, RecursionError) as exc:
            detail = str(exc)
        results.append({"check": check.model_dump(), "passed": passed, "detail": detail[:1000]})
    return results, artifacts


def candidate_fingerprint(item: dict) -> str:
    return digest({key:item.get(key, '') for key in ('feedback','cue','kind','verification_policy','replaces_id')})


def runtime_context(parent, store) -> dict:
    client = parent.client
    from dataclasses import asdict
    from nonocode.limits import RunLimits
    from urllib.parse import urlsplit,urlunsplit
    endpoint=urlsplit(str(getattr(getattr(client,"_client",None),"base_url","")))
    return {"endpoint_query_hash":hashlib.sha256(endpoint.query.encode()).hexdigest(), "run_limits":asdict(getattr(parent,"run_limits",None) or RunLimits()), "pricing":getattr(getattr(client,"_usage_config",None),"pricing",{}), "engine": ENGINE_VERSION, "protocol": parent.protocol,
            "model": getattr(client, "model", type(client).__name__),
            "endpoint": urlunsplit((endpoint.scheme,endpoint.netloc,endpoint.path,"","")),
            "instructions": parent.instructions_content, "guidance": store.guidance(),
            "context_window": parent.context_window, "tools": ["ReadFile", "WriteFile", "EditFile"]}


def verdict(runs: list[dict]) -> tuple[str, str]:
    if not runs or any(run.get("error") or not run.get("completed") for run in runs):
        return "error", "运行出错、超时或未完成，不能证明改进"
    paired: dict[tuple[str, int], dict] = {}
    for run in runs:
        paired.setdefault((run["task_id"], run["repeat"]), {})[run["arm"]] = run
    gains = {}
    for (task_id, repeat), pair in paired.items():
        if set(pair) != {"baseline", "candidate"}:
            return "error", "缺少成对执行记录"
        base = pair["baseline"]["checks"]
        candidate = pair["candidate"]["checks"]
        if not base or [row["check"] for row in base] != [row["check"] for row in candidate]:
            return "error", "固定检查不匹配"
        if not all(row["passed"] for row in candidate):
            return "failed", "候选未通过全部检查，保留当前版本"
        gains[repeat] = gains.get(repeat, False) or any(not row["passed"] for row in base)
    if len(gains) < 2 or not all(gains.values()):
        return "inconclusive", "没有在至少两轮对照中持续优于当前版本，保留当前版本"
    return "passed", "候选通过全部检查，且每轮均相对基线有改善；结论仅覆盖登记的验证任务"


class VerificationEngine:
    def __init__(self, parent, store):
        self.parent, self.store = parent, store
        self.tasks = TaskStore(parent.work_dir)
        self.runs_dir = store.directory / "runs"

    async def _execute(self, record: dict, item: dict, context: dict, arm: str, repeat: int) -> dict:
        from nonocode.agent import Agent, ErrorEvent, LoopComplete
        from nonocode.conversation import ConversationManager
        from nonocode.tools import ToolRegistry
        from nonocode.tools.edit_file import EditFile
        from nonocode.tools.file_state_cache import FileStateCache
        from nonocode.tools.read_file import ReadFile
        from nonocode.tools.write_file import WriteFile

        spec = TaskSpec.model_validate(record["spec"])
        import time
        started = time.monotonic()
        result = {"split": spec.split, "task_id": spec.id, "repeat": repeat, "arm": arm, "completed": False,
                  "error": "", "checks": [], "artifacts": {}, "trace": []}
        with tempfile.TemporaryDirectory(prefix="nonocode-probe-") as temporary:
            workspace = Path(temporary)
            for name, encoded in record["files"].items():
                target = workspace_path(workspace, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(encoded))
            registry, cache = ToolRegistry(), FileStateCache()
            for tool in (ReadFile(file_state_cache=cache), WriteFile(file_state_cache=cache), EditFile(file_state_cache=cache)):
                registry.register(WorkspaceFileTool(tool, workspace))
            guidance = context['guidance']
            if arm == 'candidate' and item.get('replaces_id'):
                previous = self.store.get(item['replaces_id'])
                line = json.dumps({'rule':previous['feedback'],'when':previous.get('cue',''),'kind':previous.get('kind','preference')},ensure_ascii=False)
                guidance = guidance.replace('- '+line, '')
            instructions = context['instructions'] + '\n' + guidance
            if arm == "candidate":
                instructions += "\nCandidate capability (only when relevant):\n" + json.dumps(
                    {"rule": item["feedback"], "when": item.get("cue", "")}, ensure_ascii=False)
            instructions = instructions.replace(str(Path(self.parent.work_dir).resolve()), str(workspace))
            agent = Agent(client=self.parent.client, registry=registry, protocol=self.parent.protocol,
                          work_dir=str(workspace), max_iterations=spec.max_iterations,
                          permission_checker=ProbeChecker(), context_window=self.parent.context_window,
                          instructions_content=instructions)
            conversation = ConversationManager()
            conversation.add_user_message(spec.prompt + "\n\nInput files: " + ", ".join(record["files"])
                                          + "\nUse the available file tools to produce the requested artifacts in this workspace.")

            async def execute():
                async for event in agent.run(conversation):
                    from nonocode.execution import current_execution
                    execution_scope=current_execution.get()
                    if execution_scope and execution_scope.run_id!=execution_scope.root_id:
                        result["tracking_run_id"]=execution_scope.run_id
                    if isinstance(event, ErrorEvent):
                        result["error"] = event.message[:2000]
                    if isinstance(event, LoopComplete):
                        result["completed"] = True

            try:
                await asyncio.wait_for(execute(), timeout=spec.timeout_seconds)
            except asyncio.TimeoutError:
                result["error"] = "试跑超时"
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"[:2000]
            result["checks"], result["artifacts"] = check_artifacts(workspace, spec.checks)
            result["input_tokens"] = agent.total_input_tokens
            result["output_tokens"] = agent.total_output_tokens
            for message in conversation.history:
                result["trace"].append({"role": message.role, "text": message.content[:4000],
                                        "tools": [{"name": call.tool_name, "arguments": call.arguments}
                                                  for call in message.tool_uses],
                                        "results": [{"id": row.tool_use_id, "text": row.content[:4000],
                                                     "is_error": row.is_error} for row in message.tool_results]})
        result['estimated_cost_usd']=None
        from nonocode.execution import current_execution
        execution_scope=current_execution.get()
        if execution_scope and result.get('tracking_run_id'):
            with execution_scope.database.connect() as connection:
                row=connection.execute('SELECT count(*),sum(cost_usd),sum(cost_usd IS NULL) FROM usage_events WHERE run_id=?',(result['tracking_run_id'],)).fetchone()
            if row[0] and not row[2]:result['estimated_cost_usd']=row[1]
        result["elapsed"] = time.monotonic()-started
        return result

    async def verify(self, candidate_id: str, task_ids: list[str], progress=None) -> dict:
        item = self.store.get(candidate_id)
        if item.get("kind") != "capability" or item["status"] != "pending":
            raise ValueError("只能验证待审核的 capability 候选")
        if not 1 <= len(task_ids) <= 3 or len(task_ids) != len(set(task_ids)):
            raise ValueError("一次须指定 1–3 个不同验证任务")
        records = [self.tasks.get(task_id) for task_id in task_ids]
        policy = item.get('verification_policy','paired-v1')
        splits = {record['spec'].get('split','development') for record in records}
        if policy == 'holdout-v1' and splits != {'development','holdout'}:
            raise ValueError('新能力验证须同时选择 development 与 holdout 任务；保留集不能参与候选改写')
        context = runtime_context(self.parent, self.store)
        # A cancelled or failed recheck must not leave an earlier passing report
        # available for adoption as though the newer attempt had never happened.
        item.pop("verification", None)
        self.store._write(item)
        from nonocode.execution import current_execution
        execution_scope=current_execution.get()
        report = {"tracking_run_id":execution_scope.root_id if execution_scope else None,"id": uuid.uuid4().hex, "candidate_id": candidate_id,
                  "candidate_fingerprint": candidate_fingerprint(item),
                  "context_fingerprint": digest(context), "runtime": context,
                  "verification_policy": policy,
                  "tasks": {record["spec"]["id"]: record["fingerprint"] for record in records},
                  "created_at": datetime.now(timezone.utc).isoformat(), "runs": []}
        abort = False
        for record in records:
            for repeat in range(record["spec"]["repetitions"]):
                # Alternate order to reduce systematic warm-up/order effects.
                arms = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                for arm in arms:
                    if progress:
                        progress(f"验证 {record['spec']['id']} 第 {repeat + 1} 轮：{arm}")
                    trial = await self._execute(record, item, context, arm, repeat)
                    report["runs"].append(trial)
                    if trial["error"] or not trial["completed"]:
                        abort = True
                        break
                if abort:
                    break
            if abort:
                break
        report['holdout_passed'] = any(run.get('split')=='holdout' for run in report['runs']) and all(
            run['completed'] and not run['error'] and all(check['passed'] for check in run['checks'])
            for run in report['runs'] if run.get('split')=='holdout' and run['arm']=='candidate')
        report["outcome"], report["reason"] = verdict(report["runs"])
        if policy=='holdout-v1' and not report['holdout_passed'] and report['outcome']=='passed':
            report['outcome'],report['reason']='failed','保留集未通过，保留当前版本' 
        report["input_tokens"] = sum(run["input_tokens"] for run in report["runs"])
        report["output_tokens"] = sum(run["output_tokens"] for run in report["runs"])
        atomic_json(self.runs_dir / f"{report['id']}.json", report)
        current = self.store.get(candidate_id)
        if current["status"] != "pending" or candidate_fingerprint(current) != report["candidate_fingerprint"]:
            raise ValueError("验证期间候选发生变化，报告已保存但不能关联；请重新验证")
        current["verification"] = {"run_id": report["id"], "outcome": report["outcome"],
                                   "fingerprint": digest(report)}
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store._write(current)
        return report


def require_verified(store, item: dict, context_fingerprint: str) -> dict:
    """Capability adoption requires a current, complete, successful execution record."""
    try:
        run_id = item["verification"]["run_id"]
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("报告 ID 无效")
        report = json.loads((store.directory / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        if (item["verification"]["fingerprint"] != digest(report)
                or report["id"] != run_id or report["candidate_id"] != item["id"]
                or report["candidate_fingerprint"] != candidate_fingerprint(item)
                or not context_fingerprint or report["context_fingerprint"] != context_fingerprint
                or report["context_fingerprint"] != digest(report["runtime"])):
            raise ValueError("候选或运行环境已变化")
        tasks = TaskStore(str(store.directory.parent.parent))
        if not report["tasks"]:
            raise ValueError("报告没有验证任务")
        expected = set()
        splits = set()
        for task_id, fingerprint in report["tasks"].items():
            record = tasks.get(task_id)
            if record["fingerprint"] != fingerprint:
                raise ValueError("验证任务已更新")
            splits.add(record["spec"].get("split","development"))
            for repeat in range(record["spec"]["repetitions"]):
                for arm in ("baseline", "candidate"):
                    expected.add((task_id, repeat, arm))
            fixed_checks = record["spec"]["checks"]
            for run in report["runs"]:
                if run["task_id"] == task_id and (
                        [row["check"] for row in run["checks"]] != fixed_checks
                        or any(type(row["passed"]) is not bool for row in run["checks"])):
                    raise ValueError("报告中的检查与冻结任务不一致")
        if item.get('verification_policy')=='holdout-v1':
            if splits != {'development','holdout'} or report.get('verification_policy')!='holdout-v1' or not report.get('holdout_passed'):
                raise ValueError('保留集验证缺失或未通过')
        actual = [(run["task_id"], run["repeat"], run["arm"]) for run in report["runs"]]
        if len(actual) != len(expected) or set(actual) != expected or verdict(report["runs"])[0] != "passed":
            raise ValueError("报告未证明持续改善")
        return report
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"能力候选尚未通过有效执行验证，不能采纳：{exc}；请先 /evolve verify") from exc
