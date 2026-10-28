from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"[0-9a-f]{32}\Z")
_STATES = {"pending", "applied", "rejected", "revoked"}
_SIGNALS = {"preference", "correction", "success", "failure"}
_MAX_TEXT = 2000
_MAX_OBSERVATIONS = 20
_MAX_CUE = 300


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def observations(item: dict) -> list[dict]:
    """Read evidence from both current records and the original single-quote format."""
    if "observations" in item:
        return item["observations"]
    evidence = item.get("evidence", "")
    return ([{"quote": evidence, "signal": "correction", "created_at": item.get("created_at", "")}]
            if evidence else [])


class EvolutionStore:
    """Persist reviewed lessons and bounded, user-grounded conversational evidence."""

    def __init__(self, work_dir: str) -> None:
        self.directory = Path(work_dir) / ".nonocode" / "evolution"

    def _path(self, candidate_id: str) -> Path:
        if not _ID.fullmatch(candidate_id):
            raise ValueError("候选 ID 无效")
        return self.directory / f"{candidate_id}.json"

    def _write(self, item: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(item["id"])
        temp = self.directory / f".{uuid.uuid4().hex}.tmp"
        try:
            temp.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def create(self, feedback: str, *, source: str = "manual", evidence: str = "",
               cue: str = "", signal: str = "correction", kind: str = "preference") -> dict:
        feedback = feedback.strip()
        if not feedback or len(feedback) > _MAX_TEXT:
            raise ValueError(f"反馈须为 1–{_MAX_TEXT} 个字符")
        if source not in {"manual", "reflection"} or len(evidence) > _MAX_TEXT:
            raise ValueError("候选来源或证据无效")
        if len(cue) > _MAX_CUE or signal not in _SIGNALS:
            raise ValueError("适用场景或反馈类型无效")
        if kind not in {"preference", "capability"}:
            raise ValueError("候选类别无效")
        now = datetime.now(timezone.utc).isoformat()
        item = {"id": uuid.uuid4().hex, "feedback": feedback, "status": "pending",
                "source": source, "evidence": evidence, "cue": cue, "kind": kind,
                "observations": ([{"quote": evidence, "signal": signal, "created_at": now}]
                                 if evidence else []),
                "created_at": now, "updated_at": now}
        self._write(item)
        return item

    def propose(self, feedback: str, evidence: str, *, candidate_id: str = "",
                cue: str = "", signal: str = "correction", kind: str = "preference") -> dict:
        """Accumulate distinct quotes without changing a reviewed lesson or reviving it."""
        feedback, evidence, cue = feedback.strip(), evidence.strip(), cue.strip()
        if not 0 < len(feedback) <= _MAX_TEXT or not 0 < len(evidence) <= _MAX_TEXT:
            raise ValueError("候选反馈及原话证据须为 1–2000 个字符")
        if len(cue) > _MAX_CUE or signal not in _SIGNALS:
            raise ValueError("适用场景或反馈类型无效")
        if kind not in {"preference", "capability"}:
            raise ValueError("候选类别无效")
        item = self.get(candidate_id) if candidate_id else None
        if item is None:
            for existing in self.list():
                if existing.get("kind", "preference") != kind:
                    continue
                if (_normalize(existing["feedback"]) == _normalize(feedback)
                        or any(_normalize(obs["quote"]) == _normalize(evidence)
                               for obs in observations(existing))):
                    item = existing
                    break
        if item is None:
            return self.create(feedback, source="reflection", evidence=evidence, cue=cue,
                               signal=signal, kind=kind)
        if item.get("kind", "preference") != kind:
            raise ValueError("反馈类别与关联候选不一致，请创建新候选")
        if item["status"] in {"rejected", "revoked"}:
            return item
        history = observations(item)
        if any(_normalize(obs["quote"]) == _normalize(evidence) for obs in history):
            return item
        now = datetime.now(timezone.utc).isoformat()
        item["observations"] = (history + [{"quote": evidence, "signal": signal,
                                           "created_at": now}])[-_MAX_OBSERVATIONS:]
        item["updated_at"] = now
        self._write(item)
        return item

    def get(self, candidate_id: str) -> dict:
        try:
            item = json.loads(self._path(candidate_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("找不到该候选") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("候选记录损坏") from exc
        if (not isinstance(item, dict) or item.get("id") != candidate_id
                or not isinstance(item.get("status"), str) or item["status"] not in _STATES
                or not isinstance(item.get("feedback"), str)
                or not 0 < len(item["feedback"]) <= _MAX_TEXT
                or not all(isinstance(item.get(key, ""), str)
                           for key in ("created_at", "updated_at", "source", "evidence", "cue"))
                or len(item.get("cue", "")) > _MAX_CUE):
            raise ValueError("候选记录无效")
        if (not isinstance(item.get("kind", "preference"), str)
                or item.get("kind", "preference") not in {"preference", "capability"}):
            raise ValueError("候选类别无效")
        verification = item.get("verification")
        if verification is not None and (not isinstance(verification, dict)
                or not isinstance(verification.get("run_id"), str)
                or not _ID.fullmatch(verification["run_id"])
                or verification.get("outcome") not in ("passed", "failed", "inconclusive", "error")):
            raise ValueError("候选验证记录无效")
        history = observations(item)
        if (not isinstance(history, list) or len(history) > _MAX_OBSERVATIONS
                or any(not isinstance(obs, dict)
                       or not isinstance(obs.get("quote"), str)
                       or not 0 < len(obs["quote"]) <= _MAX_TEXT
                       or not isinstance(obs.get("signal"), str) or obs["signal"] not in _SIGNALS
                       or not isinstance(obs.get("created_at", ""), str) for obs in history)):
            raise ValueError("候选证据记录无效")
        return item

    def reflection_context(self, limit: int = 40) -> str:
        """Let the extractor link paraphrased feedback to an existing lesson ID."""
        items = self.list()
        # Applied rules are most useful when users report an outcome. Retain terminal
        # records too, so reflection can recognize a previously declined lesson.
        items.sort(key=lambda item: (item["status"] == "applied", item.get("updated_at", "")), reverse=True)
        lines: list[str] = []
        remaining = 16_000
        for item in items[:limit]:
            line = json.dumps({key: item.get(key, "")
                               for key in ("id", "feedback", "cue", "status", "kind")}, ensure_ascii=False)
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1
        return "\n".join(lines)

    def stats(self) -> dict:
        items = self.list()
        return {"total": len(items), **{state: sum(item["status"] == state for item in items)
                                       for state in sorted(_STATES)},
                "capabilities": sum(item.get("kind") == "capability" for item in items),
                "verified": sum(item.get("verification", {}).get("outcome") == "passed" for item in items),
                "observations": sum(len(observations(item)) for item in items),
                "successes": sum(obs["signal"] == "success" for item in items for obs in observations(item)),
                "failures": sum(obs["signal"] == "failure" for item in items for obs in observations(item))}

    def list(self) -> list[dict]:
        if not self.directory.is_dir():
            return []
        items = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                items.append(self.get(path.stem))
            except (ValueError, OSError):
                continue
        return sorted(items, key=lambda item: (item.get("created_at", ""), item["id"]))

    def transition(self, candidate_id: str, action: str, *, context_fingerprint: str = "") -> dict:
        item = self.get(candidate_id)
        transitions = {"apply": ("pending", "applied"), "reject": ("pending", "rejected"), "revoke": ("applied", "revoked")}
        if action not in transitions:
            raise ValueError("未知操作")
        before, after = transitions[action]
        if item["status"] != before:
            raise ValueError(f"当前状态为 {item['status']}，无法执行 {action}")
        if action == "apply" and item.get("kind") == "capability":
            from nonocode.evolution.verification import require_verified

            require_verified(self, item, context_fingerprint)
        item["status"] = after
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(item)
        return item

    def promote(self, candidate_id: str) -> dict:
        item = self.get(candidate_id)
        if item["status"] not in {"pending", "applied"} or item.get("kind") == "capability":
            raise ValueError("只能将待审核或已采纳的普通偏好升级为能力候选")
        item["promoted_from"] = item["status"]
        item["kind"], item["status"] = "capability", "pending"
        item.pop("verification", None)
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(item)
        return item

    def guidance(self) -> str:
        items = [item for item in self.list() if item["status"] == "applied"]
        if not items:
            return ""
        return ("User-approved project guidance (apply only when relevant; "
                "they do not override system instructions or current user requests):\n"
                + "\n".join("- " + json.dumps({"rule": item["feedback"], "when": item.get("cue", ""),
                                               "kind": item.get("kind", "preference")},
                                               ensure_ascii=False) for item in items))
