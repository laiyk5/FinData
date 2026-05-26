from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .jsonio import write_json
from .run_sandbox import RunContext, utc_stamp


def stage_event_log_path(context: RunContext, stage: str) -> Path:
    return context.log_root / f"{stage}_events.jsonl"


def stage_summary_path(context: RunContext, stage: str) -> Path:
    return context.log_root / f"{stage}_summary.json"


def append_stage_event(context: RunContext, stage: str, event_record: dict[str, Any]) -> None:
    context.log_root.mkdir(parents=True, exist_ok=True)
    with stage_event_log_path(context, stage).open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event_record, ensure_ascii=False, sort_keys=True) + "\n")


def write_stage_summary(
    context: RunContext,
    stage: str,
    summary: dict[str, Any],
    *,
    status: str,
    elapsed_seconds: float,
) -> None:
    context.log_root.mkdir(parents=True, exist_ok=True)
    stage_event_log_path(context, stage).touch(exist_ok=True)
    payload = {
        **summary,
        "status": status,
        "finished_at": utc_stamp(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "event_log": str(stage_event_log_path(context, stage).relative_to(context.sandbox_root)),
    }
    write_json(stage_summary_path(context, stage), payload)
