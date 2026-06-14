from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from .dataset_specs import coverage_from_rows, get_spec
from .jsonio import read_json, write_json
from .run_sandbox import RunContext, mark_step, utc_stamp
from .stage_logs import append_stage_event, write_stage_summary
from .storage import data_files, read_table
from .workspace import dataset_docs_dir


def publish_sandbox(context: RunContext, fail_before_final_rename: bool = False) -> dict[str, Any]:
    started_at = time.monotonic()
    append_stage_event(
        context,
        "publish",
        {
            "event": "start",
            "created_at": utc_stamp(),
            "dataset": context.dataset_name,
            "run_id": context.run_id,
        },
    )
    status_path = context.qa_root / "status.json"
    if not status_path.is_file():
        raise RuntimeError("QA status is missing. Run qa before publish.")
    status = read_json(status_path)
    if not status.get("passed"):
        raise RuntimeError("QA did not pass. Publish is blocked.")

    source_current = context.sandbox_dataset_root / "current"
    if not source_current.is_dir():
        raise RuntimeError(f"Sandbox published current directory is missing: {source_current}")

    dataset_root = context.dataset_root
    current_dir = dataset_root / "current"
    next_dir = dataset_root / f"next-{context.run_id}"

    if next_dir.exists():
        raise FileExistsError(f"Next publish directory already exists: {next_dir}")

    append_stage_event(
        context,
        "publish",
        {
            "event": "copy_next",
            "created_at": utc_stamp(),
            "source_current": str(source_current.relative_to(context.sandbox_root)),
            "next_dir": str(next_dir.relative_to(dataset_root)),
        },
    )
    shutil.copytree(source_current, next_dir)
    checksums = checksum_tree(next_dir)
    append_stage_event(
        context,
        "publish",
        {
            "event": "checksums_complete",
            "created_at": utc_stamp(),
            "file_count": len(checksums),
            "next_dir": str(next_dir.relative_to(dataset_root)),
        },
    )

    # Attach dataset documentation alongside published data
    docs_dir = dataset_docs_dir(context.workspace_root, context.dataset_name)
    _copy_doc(docs_dir, dataset_root, "dataset_card.md")
    _copy_doc(docs_dir, dataset_root, "schema.yaml")

    if fail_before_final_rename:
        raise RuntimeError("Simulated failure before final rename.")

    backup_path = None
    if current_dir.exists():
        shutil.rmtree(current_dir)
        append_stage_event(
            context,
            "publish",
            {
                "event": "remove_previous_current",
                "created_at": utc_stamp(),
                "backup_path": None,
            },
        )

    next_dir.rename(current_dir)
    append_stage_event(
        context,
        "publish",
        {
            "event": "promote_next",
            "created_at": utc_stamp(),
            "current_path": str(current_dir.relative_to(dataset_root)),
        },
    )

    write_json(context.qa_root / "checksum_manifest.json", {"run_id": context.run_id, "files": checksums})
    publish_log = {
        "run_id": context.run_id,
        "dataset": context.dataset_name,
        "published_at": utc_stamp(),
        "current_path": str(current_dir.relative_to(dataset_root)),
        "backup_path": str(backup_path.relative_to(context.workspace_root)) if backup_path else None,
        "file_count": len(checksums),
    }
    write_json(context.log_root / f"{publish_log['published_at']}_publish_{context.run_id}.json", publish_log)
    update_manifest_after_publish(dataset_root, context, publish_log)
    append_stage_event(
        context,
        "publish",
        {
            "event": "done",
            "created_at": utc_stamp(),
            "published_at": publish_log["published_at"],
            "backup_path": publish_log["backup_path"],
            "file_count": publish_log["file_count"],
        },
    )
    write_stage_summary(
        context,
        "publish",
        publish_log,
        status="completed",
        elapsed_seconds=time.monotonic() - started_at,
    )
    mark_step(context, "published")
    return publish_log


def _copy_doc(docs_dir: Path, target_dir: Path, filename: str) -> None:
    """Copy a documentation file from docs/datasets/ to the published dataset root."""
    source = docs_dir / filename
    if source.is_file():
        shutil.copyfile(source, target_dir / filename)


def checksum_tree(root: Path) -> list[dict[str, str]]:
    checksums: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        checksums.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": sha256_file(path),
            }
        )
    return checksums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_manifest_after_publish(dataset_root: Path, context: RunContext, publish_log: dict[str, Any]) -> None:
    manifest_path = dataset_root / "manifest.yaml"
    coverage = published_coverage(context.dataset_name, dataset_root / "current")

    if not manifest_path.is_file():
        text = _render_initial_manifest(context, publish_log, coverage)
    else:
        text = manifest_path.read_text(encoding="utf-8")
        text = replace_block(text, "storage", render_storage_block(context.dataset_name))
        text = replace_block(text, "coverage", render_coverage_block(context.dataset_name, coverage))
        text = replace_block(text, "quality", render_quality_block(context, publish_log))
        text = replace_block(text, "missingness", render_missingness_block(context, publish_log))
        text = replace_block(text, "publication", render_publication_block(context, publish_log))
        replacements = {"status:": "status: published"}
        lines = []
        for line in text.splitlines():
            replacement = None
            for prefix, value in replacements.items():
                if line.startswith(prefix):
                    replacement = value
                    break
            lines.append(replacement or line)
        text = "\n".join(lines) + "\n"

    manifest_path.write_text(text, encoding="utf-8")


def published_coverage(dataset_name: str, current_dir: Path) -> dict[str, Any]:
    spec = get_spec(dataset_name)
    rows: list[dict[str, str]] = []
    for data_path in data_files(current_dir, spec):
        rows.extend(read_table(data_path, spec))
    return coverage_from_rows(dataset_name, rows)


def render_coverage_block(dataset_name: str, coverage: dict[str, Any]) -> list[str]:
    if dataset_name == "report_catalog":
        return [
            "coverage:",
            "  universes:",
            *[f"    - {universe_id}" for universe_id in coverage["universes"]],
            f"  start_year: {coverage['start_year'] or 'null'}",
            f"  end_year: {coverage['end_year'] or 'null'}",
            f"  report_count: {coverage['report_count']}",
            "  symbols:",
            *[f"    - {symbol}" for symbol in coverage["symbols"]],
        ]
    if dataset_name == "tushare_index_weight":
        return [
            "coverage:",
            "  index_codes:",
            *[f"    - {index_code}" for index_code in coverage["index_codes"]],
            f"  start_date: {coverage['start_date'] or 'null'}",
            f"  end_date: {coverage['end_date'] or 'null'}",
            "  index_ranges:",
            *[
                line
                for index_code in coverage["index_codes"]
                for line in (
                    f"    {index_code}:",
                    f"      start_date: {coverage['index_ranges'][index_code]['start_date'] or 'null'}",
                    f"      end_date: {coverage['index_ranges'][index_code]['end_date'] or 'null'}",
                    f"      latest_snapshot_date: {coverage['latest_snapshot_dates'][index_code] or 'null'}",
                    f"      latest_constituent_count: {coverage['latest_constituent_counts'][index_code]}",
                )
            ],
        ]
    if dataset_name == "trade_calendar":
        return [
            "coverage:",
            "  exchanges:",
            *[f"    - {exchange}" for exchange in coverage["exchanges"]],
            f"  start_date: {coverage['start_date'] or 'null'}",
            f"  end_date: {coverage['end_date'] or 'null'}",
            "  exchange_ranges:",
            *[
                line
                for exchange in coverage["exchanges"]
                for line in (
                    f"    {exchange}:",
                    f"      start_date: {coverage['exchange_ranges'][exchange]['start_date'] or 'null'}",
                    f"      end_date: {coverage['exchange_ranges'][exchange]['end_date'] or 'null'}",
                )
            ],
        ]
    return [
        "coverage:",
        "  symbols:",
        *[f"    - {symbol}" for symbol in coverage["symbols"]],
        f"  start_date: {coverage['start_date'] or 'null'}",
        f"  end_date: {coverage['end_date'] or 'null'}",
        "  calendar: CN_A_SHARE",
        "  symbol_ranges:",
        *[
            line
            for symbol in coverage["symbols"]
            for line in (
                f"    {symbol}:",
                f"      start_date: {coverage['symbol_ranges'][symbol]['start_date'] or 'null'}",
                f"      end_date: {coverage['symbol_ranges'][symbol]['end_date'] or 'null'}",
            )
        ],
    ]


def render_storage_block(dataset_name: str) -> list[str]:
    from .dataset_specs import get_spec
    spec = get_spec(dataset_name)
    return [
        "storage:",
        f"  format: {spec.storage_format}",
        f"  partitioning: {spec.output_partition_field or spec.publish_partition_field or 'none'}",
        "  published_current: current",
        "  published_current_purpose: consumer-facing latest published version",
        "  backups: disabled",
        "  run_sandbox_root: ../../sandboxes/runs",
        "  provider_cache_root: ../../cache",
    ]


def render_quality_block(context: RunContext, publish_log: dict[str, Any]) -> list[str]:
    return [
        "quality:",
        "  validation_status: passed",
        f"  last_validation_report: sandboxes/runs/{context.dataset_name}/{context.run_id}/qa/validation_report.json",
    ]


def render_missingness_block(context: RunContext, publish_log: dict[str, Any]) -> list[str]:
    return [
        "missingness:",
        "  status: assessed",
        f"  file: sandboxes/runs/{context.dataset_name}/{context.run_id}/qa/missingness_report.json",
    ]


def render_publication_block(context: RunContext, publish_log: dict[str, Any]) -> list[str]:
    return [
        "publication:",
        f"  last_published_at: {publish_log['published_at']}",
        "  current_version_path: current",
        f"  backup_path: {publish_log.get('backup_path') or 'null'}",
    ]


def _render_initial_manifest(context: RunContext, publish_log: dict[str, Any], coverage: dict[str, Any]) -> str:
    """Create an initial manifest.yaml for a new dataset."""
    blocks: list[str] = [
        f"dataset: {context.dataset_name}",
        "status: published",
        "version: 0.1.0",
        "",
    ]
    blocks.extend(render_storage_block(context.dataset_name))
    blocks.append("")
    blocks.extend(render_coverage_block(context.dataset_name, coverage))
    blocks.append("")
    blocks.extend(render_quality_block(context, publish_log))
    blocks.append("")
    blocks.extend(render_missingness_block(context, publish_log))
    blocks.append("")
    blocks.extend(render_publication_block(context, publish_log))
    blocks.append("")
    return "\n".join(blocks)


def replace_block(text: str, block_name: str, replacement: list[str]) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    block_header = f"{block_name}:"

    for line in lines:
        if not skipping and line == block_header:
            output.extend(replacement)
            skipping = True
            continue
        if skipping:
            if line and not line.startswith(" "):
                skipping = False
                output.append(line)
            continue
        output.append(line)

    if skipping:
        return "\n".join(output)
    return "\n".join(output)
