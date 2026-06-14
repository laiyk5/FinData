from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dataset_specs import DatasetSpec


PARQUET_DATASETS = {"tushare_daily", "tushare_adj_factor", "tushare_index_weight"}


def uses_parquet(spec: DatasetSpec) -> bool:
    return spec.storage_format == "parquet"


def partition_value_for_row(row: dict[str, str], spec: DatasetSpec) -> str:
    value = row.get(spec.partition_field, "")
    if spec.partition_granularity == "month":
        return value[:6]
    return value


def partition_dir_name(spec: DatasetSpec, partition_value: str) -> str:
    field = spec.output_partition_field or spec.partition_field
    return f"{field}={partition_value}"


def data_files(root: Path, spec: DatasetSpec) -> list[Path]:
    if not root.exists():
        return []
    patterns = ("*.parquet", "*.csv") if uses_parquet(spec) else ("*.csv",)
    files: list[Path] = []
    for pattern in patterns:
        files.extend(root.rglob(pattern))
    return sorted(files)


def clear_data_tree(root: Path, spec: DatasetSpec) -> None:
    for path in data_files(root, spec):
        path.unlink()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def read_table(path: Path, spec: DatasetSpec) -> list[dict[str, str]]:
    if path.suffix == ".parquet":
        return read_parquet(path, spec)
    return read_csv(path, spec)


def write_table(path: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    if uses_parquet(spec):
        write_parquet(path, rows, spec)
    else:
        write_csv(path, rows, spec)


def read_csv(path: Path, spec: DatasetSpec) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as input_file:
        return [{field: str(row.get(field, "")) for field in spec.fields} for row in csv.DictReader(input_file)]


def write_csv(path: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=spec.fields)
        writer.writeheader()
        writer.writerows(rows)


def read_parquet(path: Path, spec: DatasetSpec) -> list[dict[str, str]]:
    pd = import_pandas()
    frame = pd.read_parquet(path)
    rows: list[dict[str, str]] = []
    for record in frame.to_dict(orient="records"):
        rows.append({field: normalize_cell(record.get(field, "")) for field in spec.fields})
    return rows


def write_parquet(path: Path, rows: list[dict[str, str]], spec: DatasetSpec) -> None:
    pd = import_pandas()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=list(spec.fields))
    frame.to_parquet(path, engine="pyarrow", index=False)


def normalize_cell(value: object) -> str:
    if value is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def import_pandas():
    try:
        import pandas as pd
        import pyarrow  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Parquet datasets require pandas and pyarrow. Install maintool dependencies first.") from exc
    return pd
