from __future__ import annotations

import time
import csv
from pathlib import Path
from typing import Any

from .cninfo import finalize_report_versions, should_keep_report_row
from .dataset_specs import DatasetSpec, get_spec, parse_symbols
from .jsonio import read_json, write_json
from .run_sandbox import RunContext, load_prepare_ledger, mark_step, utc_stamp
from .stage_logs import append_stage_event, write_stage_summary


def ingest_prepared_raw(context: RunContext) -> dict[str, Any]:
    started_at = time.monotonic()
    spec = get_spec(context.dataset_name)
    ledger = load_prepare_ledger(context)
    prepared_rows: list[dict[str, str]] = []
    source_requests = 0
    source_items = 0

    append_stage_event(
        context,
        "ingest",
        {
            "event": "start",
            "created_at": utc_stamp(),
            "dataset": context.dataset_name,
            "request_count": len(ledger["requests"]),
        },
    )

    for request_index, request in enumerate(ledger["requests"].values(), start=1):
        if request["status"] != "success":
            continue
        if not request["raw_path"]:
            continue
        raw_path = context.sandbox_root / request["raw_path"]
        if not raw_path.is_file():
            continue
        source_requests += 1
        payload = read_json(raw_path)
        items = list(payload.get("items", []))
        source_items += len(items)
        append_stage_event(
            context,
            "ingest",
            {
                "event": "request",
                "created_at": utc_stamp(),
                "index": request_index,
                "request_key": request.get("key"),
                "raw_path": request["raw_path"],
                "item_count": len(items),
                "prepared_rows_so_far": len(prepared_rows),
            },
        )
        kept_rows = 0
        for item in items:
            row = {field: normalize_value(item.get(field, "")) for field in spec.fields}
            if should_ingest_row(context.dataset_name, request, row):
                prepared_rows.append(row)
                kept_rows += 1
        append_stage_event(
            context,
            "ingest",
            {
                "event": "request_done",
                "created_at": utc_stamp(),
                "index": request_index,
                "request_key": request.get("key"),
                "item_count": len(items),
                "kept_rows": kept_rows,
                "prepared_rows_so_far": len(prepared_rows),
            },
        )

    write_staged_files(context.sandbox_dataset_root, prepared_rows, spec)
    merged_rows = merge_current_rows(context.sandbox_dataset_root, prepared_rows, spec)

    report = {
        "run_id": context.run_id,
        "source_requests": source_requests,
        "source_items": source_items,
        "prepared_rows": len(prepared_rows),
        "published_rows_after_merge": len(merged_rows),
        "finished_at": utc_stamp(),
    }
    write_json(context.sandbox_root / "ingest_report.json", report)
    append_stage_event(
        context,
        "ingest",
        {
            "event": "done",
            "created_at": utc_stamp(),
            "source_requests": source_requests,
            "source_items": source_items,
            "prepared_rows": len(prepared_rows),
            "published_rows_after_merge": len(merged_rows),
        },
    )
    write_stage_summary(
        context,
        "ingest",
        report,
        status="completed",
        elapsed_seconds=time.monotonic() - started_at,
    )
    mark_step(context, "ingested")
    return report


def should_ingest_row(dataset_name: str, request: dict[str, Any], row: dict[str, str]) -> bool:
    if dataset_name == "report_catalog":
        return should_keep_report_row(row)
    if dataset_name not in {"tushare_daily", "tushare_daily_basic"}:
        return True

    requested_symbols = set(request.get("symbols") or parse_symbols(request.get("ts_code", "")))
    if requested_symbols and row.get("ts_code") not in requested_symbols:
        return False

    expected_trade_dates = set(request.get("expected_trade_dates") or [])
    if expected_trade_dates and row.get("trade_date") not in expected_trade_dates:
        return False

    return True


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def write_staged_files(dataset_root: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    staged_dir = dataset_root / "data" / "staged"
    staged_dir.mkdir(parents=True, exist_ok=True)

    rows_by_date: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        rows_by_date.setdefault(row[spec.partition_field], []).append(row)

    for partition_value, partition_rows in rows_by_date.items():
        write_csv(staged_dir / spec.staged_filename(partition_value), partition_rows, spec)


def merge_current_rows(dataset_root: Path, prepared_rows: list[dict[str, str]], spec: DatasetSpec) -> list[dict[str, str]]:
    current_dir = dataset_root / "data" / "published" / "current"
    current_dir.mkdir(parents=True, exist_ok=True)

    merged: dict[tuple[str, str], dict[str, str]] = {}
    for csv_path in sorted(current_dir.rglob("*.csv")):
        for row in read_csv(csv_path, spec):
            merged[spec.row_key(row)] = row

    for row in prepared_rows:
        merged[spec.row_key(row)] = row

    rows = sorted(merged.values(), key=spec.sort_key)
    if spec.name == "report_catalog":
        rows = sorted(finalize_report_versions(rows), key=spec.sort_key)
    clear_csv_tree(current_dir)
    write_current_files(current_dir, rows, spec)
    return rows


def write_current_files(current_dir: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    if not spec.publish_partition_field:
        write_csv(current_dir / spec.published_filename, rows, spec)
        return

    partitioned_rows: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        partition_value = row.get(spec.publish_partition_field, "")
        partitioned_rows.setdefault(partition_value, []).append(row)

    for partition_value, partition_rows in sorted(partitioned_rows.items()):
        partition_dir = current_dir / f"{spec.publish_partition_field}={partition_value}"
        write_csv(partition_dir / spec.published_filename, partition_rows, spec)


def clear_csv_tree(root: Path) -> None:
    for csv_path in sorted(root.rglob("*.csv")):
        csv_path.unlink()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def read_csv(path: Path, spec: DatasetSpec) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as input_file:
        return [{field: str(row.get(field, "")) for field in spec.fields} for row in csv.DictReader(input_file)]


def write_csv(path: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=spec.fields)
        writer.writeheader()
        writer.writerows(rows)
