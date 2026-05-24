from __future__ import annotations

from pathlib import Path
from typing import Any

from .jsonio import read_json
from .run_sandbox import RunContext


def build_review(context: RunContext) -> str:
    if not context.sandbox_root.is_dir():
        raise FileNotFoundError(f"Run sandbox not found: {context.sandbox_root}")

    manifest = read_optional_json(context.run_manifest_path)
    ledger = read_optional_json(context.prepare_ledger_path)
    ingest = read_optional_json(context.sandbox_root / "ingest_report.json")
    qa_status = read_optional_json(context.qa_root / "status.json")
    validation = read_optional_json(context.qa_root / "validation_report.json")
    missingness = read_optional_json(context.qa_root / "missingness_report.json")
    unusual = read_optional_json(context.qa_root / "unusual_values_report.json")

    lines: list[str] = []
    lines.extend(render_header(context, manifest))
    lines.extend(render_steps(manifest))
    lines.extend(render_prepare(ledger, ingest))
    lines.extend(render_ingest(ingest))
    lines.extend(render_qa(qa_status, validation, missingness, unusual))
    lines.extend(render_recommendation(manifest, ledger, qa_status, validation, missingness))
    return "\n".join(lines).rstrip() + "\n"


def read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return read_json(path)


def render_header(context: RunContext, manifest: dict[str, Any] | None) -> list[str]:
    if not manifest:
        return [
            f"Run Review: {context.dataset_name}/{context.run_id}",
            f"Sandbox: {context.sandbox_root}",
            "",
            "Run manifest is missing.",
            "",
        ]

    calendar_request = manifest.get("calendar_request") or {}
    universe_request = manifest.get("universe_request") or {}
    daily_request = manifest.get("daily_request") or {}
    symbols = ", ".join(manifest.get("symbols", [])) or "-"
    trade_dates = ", ".join(manifest.get("trade_dates", [])) or "-"
    settings = manifest.get("request_settings", {})
    request_plan = manifest.get("request_plan", {})
    return [
        f"Run Review: {manifest.get('dataset', context.dataset_name)}/{manifest.get('run_id', context.run_id)}",
        f"Provider: {manifest.get('provider', '-')}",
        f"Created: {manifest.get('created_at', '-')}",
        f"Symbols: {symbols}",
        f"Symbol selector: {manifest.get('symbol_selector') or '-'}",
        f"Symbol selector resolved at: {manifest.get('symbol_selector_resolved_at') or '-'}",
        f"Trade dates: {trade_dates}",
        f"Daily request: {format_daily_request(daily_request)}",
        f"Calendar request: {format_calendar_request(calendar_request)}",
        f"Universe request: {format_universe_request(universe_request)}",
        f"Request plan: {format_request_plan(request_plan)}",
        f"Rate limit seconds: {settings.get('rate_limit_seconds', '-')}",
        f"Max retries: {settings.get('max_retries', '-')}",
        f"Sandbox: {context.sandbox_root}",
        "",
    ]


def render_steps(manifest: dict[str, Any] | None) -> list[str]:
    lines = ["Lifecycle:"]
    steps = (manifest or {}).get("steps", {})
    for step in ("planned", "prepared", "ingested", "qa_passed", "published"):
        lines.append(f"- {step}: {status_word(steps.get(step))}")
    lines.append("")
    return lines


def render_prepare(ledger: dict[str, Any] | None, ingest: dict[str, Any] | None = None) -> list[str]:
    lines = ["Prepare:"]
    if not ledger:
        return lines + ["- status: not run", ""]

    requests = ledger.get("requests", {})
    counts = count_by(requests.values(), "status")
    error_counts = count_by(
        (request for request in requests.values() if request.get("error_type")),
        "error_type",
    )
    row_count = sum(int(request.get("row_count") or 0) for request in requests.values())
    if row_count == 0 and ingest:
        row_count = int(ingest.get("prepared_rows") or 0)
    attempts = sum(int(request.get("attempts") or 0) for request in requests.values())
    lines.extend(
        [
            f"- requests: {len(requests)}",
            f"- success: {counts.get('success', 0)}",
            f"- pending: {counts.get('pending', 0)}",
            f"- failed: {counts.get('failed', 0)}",
            f"- attempts: {attempts}",
            f"- rows prepared: {row_count}",
        ]
    )
    if error_counts:
        lines.append(f"- error types: {format_counts(error_counts)}")
    lines.append("")
    return lines


def render_ingest(ingest: dict[str, Any] | None) -> list[str]:
    lines = ["Ingest:"]
    if not ingest:
        return lines + ["- status: not run", ""]
    lines.extend(
        [
            f"- prepared rows: {ingest.get('prepared_rows', '-')}",
            f"- published rows after merge: {ingest.get('published_rows_after_merge', '-')}",
            f"- finished: {ingest.get('finished_at', '-')}",
            "",
        ]
    )
    return lines


def render_qa(
    qa_status: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    missingness: dict[str, Any] | None,
    unusual: dict[str, Any] | None,
) -> list[str]:
    lines = ["QA:"]
    if not qa_status:
        return lines + ["- status: not run", ""]

    lines.extend(
        [
            f"- passed: {qa_status.get('passed', False)}",
            f"- validation passed: {qa_status.get('validation_passed', False)}",
            f"- missingness blocks publish: {qa_status.get('missingness_blocks_publish', False)}",
            f"- warning count: {qa_status.get('warning_count', 0)}",
        ]
    )

    if validation:
        lines.append(f"- validation errors: {validation.get('error_count', 0)}")
        for error in validation.get("errors", [])[:5]:
            lines.append(f"  - {error}")
    if missingness:
        lines.append(f"- missing rows: {missingness.get('missing_count', 0)}")
        for record in missingness.get("missing", [])[:5]:
            lines.append(
                "  - "
                f"{record.get('ts_code')} {record.get('trade_date')} "
                f"{record.get('reason')} {record.get('status')}"
            )
    if unusual:
        warnings = unusual.get("warnings", [])
        for warning in warnings[:5]:
            lines.append(f"  - warning: {warning}")
    lines.append("")
    return lines


def render_recommendation(
    manifest: dict[str, Any] | None,
    ledger: dict[str, Any] | None,
    qa_status: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    missingness: dict[str, Any] | None,
) -> list[str]:
    steps = (manifest or {}).get("steps", {})
    requests = (ledger or {}).get("requests", {})
    failed = [request for request in requests.values() if request.get("status") == "failed"]
    pending = [request for request in requests.values() if request.get("status") == "pending"]

    if failed or pending:
        action = "Rerun prepare after checking provider errors."
    elif not steps.get("prepared"):
        action = "Run prepare."
    elif not steps.get("ingested"):
        action = "Run ingest."
    elif not qa_status:
        action = "Run qa."
    elif not qa_status.get("passed"):
        action = qa_failure_action(validation, missingness)
    elif not steps.get("published"):
        action = "QA passed. Run publish when you are ready."
    else:
        action = "Run is published. No immediate action required."

    return ["Recommended next action:", f"- {action}", ""]


def qa_failure_action(validation: dict[str, Any] | None, missingness: dict[str, Any] | None) -> str:
    if validation and validation.get("error_count", 0):
        return "Fix validation errors, then rerun ingest or qa."
    if missingness and missingness.get("blocks_publish"):
        return "Investigate unknown missingness or record accepted missingness, then rerun qa."
    return "Inspect QA reports, then rerun qa."


def count_by(items, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def format_calendar_request(calendar_request: dict[str, Any]) -> str:
    if not calendar_request:
        return "-"
    return (
        f"exchange={calendar_request.get('exchange', '-')}, "
        f"start={calendar_request.get('start_date', '-')}, "
        f"end={calendar_request.get('end_date', '-')}, "
        f"is_open={calendar_request.get('is_open')}"
    )


def format_daily_request(daily_request: dict[str, Any]) -> str:
    if not daily_request:
        return "-"
    expected_count = len(daily_request.get("expected_trade_dates") or [])
    return (
        f"start={daily_request.get('start_date', '-')}, "
        f"end={daily_request.get('end_date', '-')}, "
        f"expected_trade_dates={expected_count}"
    )


def format_universe_request(universe_request: dict[str, Any]) -> str:
    if not universe_request:
        return "-"
    return (
        f"universe_id={universe_request.get('universe_id', '-')}, "
        f"source_id={universe_request.get('source_id', '-')}, "
        f"start={universe_request.get('start_date', '-')}, "
        f"end={universe_request.get('end_date', '-')}"
    )


def format_request_plan(request_plan: dict[str, Any]) -> str:
    if not request_plan:
        return "-"
    modes = request_plan.get("modes") or {}
    mode_text = format_counts(modes) if modes else "-"
    row_limit = request_plan.get("row_limit")
    max_rows = request_plan.get("max_estimated_rows_per_request")
    if row_limit is None:
        return f"requests={request_plan.get('request_count', '-')}, modes={mode_text}"
    return (
        f"requests={request_plan.get('request_count', '-')}, "
        f"modes={mode_text}, "
        f"max_estimated_rows={max_rows}, "
        f"row_limit={row_limit}"
    )


def status_word(value: Any) -> str:
    if value is True:
        return "done"
    if value is False:
        return "not done"
    return "unknown"
