import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from src.models import AgentResult


def get_default_store_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "inquiry_logs.json"


def _load_records(store_path: Path) -> list[dict[str, Any]]:
    if not store_path.exists():
        return []
    try:
        return json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_records(store_path: Path, records: list[dict[str, Any]]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", delete=False, dir=store_path.parent, encoding="utf-8") as tmp:
        json.dump(records, tmp, ensure_ascii=False, indent=2)
        temp_path = Path(tmp.name)
    temp_path.replace(store_path)


def _build_routing_bucket(result: AgentResult) -> str:
    if result.triage_result.handoff_needed:
        return "human_handoff"
    return result.processing_path


def _build_relevant_articles(result: AgentResult) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for subtask in result.subtasks:
        for attempt in subtask.tool_results:
            for tool_result in attempt:
                for item in tool_result.results:
                    key = (tool_result.tool_name, item.file_name, item.content)
                    if key in seen:
                        continue
                    seen.add(key)
                    articles.append(
                        {
                            "tool_name": tool_result.tool_name,
                            "source": item.file_name,
                            "excerpt": item.content[:400],
                        }
                    )
    return articles[:8]


def append_inquiry_record(result: AgentResult, store_path: Path | None = None) -> None:
    path = store_path or get_default_store_path()
    records = _load_records(path)
    triage = result.triage_result
    records.append(
        {
            "id": str(uuid4()),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "inquiry": result.inquiry,
            "processing_path": result.processing_path,
            "routing_bucket": _build_routing_bucket(result),
            "resolution_mode": result.task_evaluation.resolution_mode if result.task_evaluation else "",
            "category": triage.category,
            "priority": triage.priority,
            "assigned_team": triage.assigned_team,
            "needs_follow_up": triage.needs_follow_up,
            "handoff_needed": triage.handoff_needed,
            "handoff_target": triage.handoff_target,
            "handoff_reason": triage.handoff_reason,
            "handoff_payload": triage.handoff_payload,
            "resolved_parts": triage.resolved_parts,
            "unresolved_parts": triage.unresolved_parts,
            "blocking_items": triage.blocking_items,
            "optional_context": triage.optional_context,
            "immediate_guidance": triage.immediate_guidance,
            "draft_reply": triage.draft_reply,
            "next_user_action": triage.next_user_action,
            "confidence": triage.confidence,
            "reasoning_summary": triage.reasoning_summary,
            "relevant_articles": _build_relevant_articles(result),
        }
    )
    _write_records(path, records)


def load_inquiry_records(store_path: Path | None = None) -> list[dict[str, Any]]:
    path = store_path or get_default_store_path()
    return _load_records(path)
