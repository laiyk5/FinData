from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, TextIO

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from findata.loader import DataLoader


@dataclass(frozen=True, slots=True)
class ExportOutcome:
    rows: int
    path: str
    publication_id: str
    partial_allowed: bool


def execute_data_command(
    workspace: Path,
    args: Any,
    *,
    stdout: TextIO,
) -> object:
    dataset = DataLoader(workspace).dataset(str(args.dataset))
    if args.action == "schema":
        return dataset.describe()
    if args.action == "coverage":
        return _coverage(dataset, args)

    query = _query_arguments(args, dataset.describe())
    if args.action == "preview":
        table = dataset.query(**query, limit=args.limit)
        return {
            "dataset": args.dataset,
            "publication_id": dataset.publication_id,
            "partial_allowed": bool(args.allow_partial),
            "items": table.to_pylist(),
        }
    if args.action == "export":
        return _export(dataset, args, query=query, stdout=stdout)
    raise ValueError(f"unsupported data action {args.action!r}")


def _coverage(dataset: Any, args: Any) -> dict[str, object]:
    if bool(args.range_start) != bool(args.range_end):
        raise ValueError("--from and --to must be supplied together")
    keys = _values(args.keys)
    rows = dataset.coverage(keys=keys).to_pylist()
    if not args.range_start:
        return {"dataset": args.dataset, "items": rows}

    request_start = _date_argument(args.range_start, "--from")
    request_end = _date_argument(args.range_end, "--to")
    if request_start >= request_end:
        raise ValueError("--from must be earlier than --to")
    by_key = {row["key"]: row for row in rows}
    selected_keys = keys or list(by_key)
    items: list[dict[str, object]] = []
    for key in selected_keys:
        row = by_key.get(key)
        missing: list[tuple[date, date]] = []
        if row is None:
            covered_start = covered_end = None
            missing.append((request_start, request_end))
        else:
            covered_start, covered_end = row["start"], row["end"]
            if request_start < covered_start:
                missing.append((request_start, min(request_end, covered_start)))
            if request_end > covered_end:
                missing.append((max(request_start, covered_end), request_end))
            missing = [(start, end) for start, end in missing if start < end]
        items.append(
            {
                "key": key,
                "requested_start": request_start,
                "requested_end": request_end,
                "complete": not missing,
                "covered_start": covered_start,
                "covered_end": covered_end,
                "missing": missing,
            }
        )
    return {"dataset": args.dataset, "items": items}


def _date_argument(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{option} must be an ISO date (YYYY-MM-DD)") from exc


def _query_arguments(args: Any, description: dict[str, Any]) -> dict[str, Any]:
    if args.require_coverage and args.allow_partial:
        raise ValueError("--require-coverage and --allow-partial are mutually exclusive")
    if bool(args.range_start) != bool(args.range_end):
        raise ValueError("--from and --to must be supplied together")
    keys = _values(args.keys)
    time_range = (args.range_start, args.range_end) if args.range_start and args.range_end else None
    require_coverage = bool(
        args.require_coverage
        or (keys is not None and time_range is not None and not args.allow_partial)
    )
    columns = _columns(args.columns)
    field_names = [field["name"] for field in description["fields"]]
    selected = set(columns or field_names)
    order_by = [key for key in description["primary_key"] if key in selected]
    return {
        "keys": keys,
        "time_range": time_range,
        "columns": columns,
        "order_by": order_by,
        "require_coverage": require_coverage,
    }


def _export(dataset: Any, args: Any, *, query: dict[str, Any], stdout: TextIO) -> ExportOutcome:
    target_text = str(args.output)
    if target_text == "-":
        binary = getattr(stdout, "buffer", None)
        if args.export_format in {"parquet", "arrow"} and binary is None:
            raise ValueError(f"{args.export_format} stdout export requires a binary stdout stream")
        sink: BinaryIO | TextIO = binary if binary is not None else stdout
        rows, publication = _write_batches(
            dataset,
            sink,
            output_format=args.export_format,
            batch_size=args.batch_size,
            query=query,
        )
        return ExportOutcome(rows, "-", publication, bool(args.allow_partial))

    target = Path(target_text).expanduser().resolve()
    if target.exists() and not args.force:
        raise ValueError(f"output file already exists: {target}; use --force to replace it")
    if not target.parent.is_dir():
        raise ValueError(f"output directory does not exist: {target.parent}")
    temporary_file = tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
    )
    temporary = Path(temporary_file.name)
    temporary_file.close()
    try:
        with temporary.open("wb") as sink:
            rows, publication = _write_batches(
                dataset,
                sink,
                output_format=args.export_format,
                batch_size=args.batch_size,
                query=query,
            )
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return ExportOutcome(rows, str(target), publication, bool(args.allow_partial))


def _write_batches(
    dataset: Any,
    sink: BinaryIO | TextIO,
    *,
    output_format: str,
    batch_size: int,
    query: dict[str, Any],
) -> tuple[int, str]:
    rows = 0
    with dataset.iter_batches(batch_size=batch_size, **query) as batches:
        schema = batches.schema
        writer: Any
        text_csv = output_format == "csv" and not (
            isinstance(sink, pa.NativeFile) or "b" in getattr(sink, "mode", "")
        )
        if text_csv:
            writer = None
        elif output_format == "csv":
            writer = pacsv.CSVWriter(sink, schema)
        elif output_format == "parquet":
            writer = pq.ParquetWriter(sink, schema)
        elif output_format == "arrow":
            writer = ipc.new_file(sink, schema)
        elif output_format == "jsonl":
            writer = None
        else:
            raise ValueError(f"unsupported export format {output_format!r}")
        try:
            for batch in batches:
                if text_csv:
                    buffer = pa.BufferOutputStream()
                    pacsv.write_csv(
                        batch,
                        buffer,
                        write_options=pacsv.WriteOptions(include_header=rows == 0),
                    )
                    sink.write(buffer.getvalue().to_pybytes().decode("utf-8"))
                elif writer is None:
                    for row in batch.to_pylist():
                        value = json.dumps(row, separators=(",", ":"), default=_json_default) + "\n"
                        if isinstance(sink, pa.NativeFile) or "b" in getattr(sink, "mode", ""):
                            sink.write(value.encode("utf-8"))
                        else:
                            sink.write(value)
                else:
                    writer.write_batch(batch)
                rows += batch.num_rows
        finally:
            if writer is not None:
                writer.close()
        assert batches.publication_id is not None
        return rows, batches.publication_id


def _values(values: tuple[str, ...]) -> list[str] | None:
    return list(dict.fromkeys(values)) or None


def _columns(values: tuple[str, ...]) -> list[str] | None:
    result = [item for value in values for item in value.split(",") if item]
    return list(dict.fromkeys(result)) or None


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")
