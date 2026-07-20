from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from findata.storage import (
    COVERAGE_SCHEMA,
    MANIFEST_VERSION,
    DatasetGate,
    decode_manifest_schema,
    load_manifest,
)


class DataLoaderError(RuntimeError):
    pass


class DatasetNotReadyError(DataLoaderError):
    pass


class IncompatibleDatasetError(DataLoaderError):
    pass


class UnsupportedCoverageError(DataLoaderError):
    pass


class QueryError(DataLoaderError):
    pass


class CoverageError(DataLoaderError):
    def __init__(
        self,
        dataset: str,
        missing_intervals: Mapping[str, list[tuple[date, date]]],
    ) -> None:
        self.dataset = dataset
        self.missing_intervals = dict(missing_intervals)
        super().__init__(f"{dataset} has unresolved coverage: {self.missing_intervals!r}")


class DataLoader:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def dataset(self, name: str) -> DatasetReader:
        return DatasetReader(self.workspace / "datasets" / name, name=name)


class DatasetReader:
    def __init__(self, dataset_root: Path, *, name: str) -> None:
        self.dataset_root = dataset_root
        self.name = name

    @property
    def publication_id(self) -> str:
        with DatasetGate(self.dataset_root / "gate.lock", exclusive=False):
            manifest = self._ready_manifest()
            return str(manifest["publication_id"])

    def query(
        self,
        *,
        keys: Sequence[str] | None = None,
        time_range: tuple[str | date, str | date] | None = None,
        columns: Sequence[str] | None = None,
        filters: Sequence[tuple[str, str, Any]] | None = None,
        order_by: Sequence[str | tuple[str, str]] | None = None,
        limit: int | None = None,
        require_coverage: bool = False,
    ) -> pa.Table:
        with DatasetGate(self.dataset_root / "gate.lock", exclusive=False):
            manifest = self._ready_manifest()
            return self._query_locked(
                manifest,
                keys=keys,
                time_range=time_range,
                columns=columns,
                filters=filters,
                order_by=order_by,
                limit=limit,
                require_coverage=require_coverage,
            )

    def iter_batches(self, *, batch_size: int = 65_536, **query: Any) -> BatchReader:
        if batch_size <= 0:
            raise QueryError("batch_size must be positive")
        return BatchReader(self, batch_size=batch_size, query=query)

    def coverage(self, keys: Sequence[str] | None = None) -> pa.Table:
        with DatasetGate(self.dataset_root / "gate.lock", exclusive=False):
            manifest = self._ready_manifest()
            table = self._read_coverage(manifest)
            if keys is not None:
                table = table.filter(
                    pc.is_in(table["key"], value_set=pa.array(list(keys), type=pa.string()))
                )
            return table

    def _ready_manifest(self) -> dict[str, Any]:
        try:
            manifest = load_manifest(self.dataset_root)
        except Exception as exc:
            raise DataLoaderError(f"cannot load dataset {self.name!r}") from exc
        if manifest.get("manifest_version") != MANIFEST_VERSION:
            raise IncompatibleDatasetError(
                f"dataset {self.name!r} uses manifest version {manifest.get('manifest_version')!r}"
            )
        if manifest.get("data_layout_version") != 1:
            raise IncompatibleDatasetError(
                f"dataset {self.name!r} uses data layout version "
                f"{manifest.get('data_layout_version')!r}"
            )
        if manifest.get("state") != "ready" or not manifest.get("publication_id"):
            raise DatasetNotReadyError(f"dataset {self.name!r} has no published snapshot")
        return manifest

    def _query_locked(
        self,
        manifest: Mapping[str, Any],
        *,
        keys: Sequence[str] | None,
        time_range: tuple[str | date, str | date] | None,
        columns: Sequence[str] | None,
        filters: Sequence[tuple[str, str, Any]] | None,
        order_by: Sequence[str | tuple[str, str]] | None,
        limit: int | None,
        require_coverage: bool,
    ) -> pa.Table:
        schema = decode_manifest_schema(manifest)
        normalized_range = _normalize_time_range(time_range)
        if require_coverage:
            self._enforce_coverage(manifest, keys=keys, time_range=normalized_range)
        table = self._read_table(manifest, schema)
        partition_key = manifest.get("partition_key")
        time_field = manifest.get("time_field")
        if keys is not None:
            if not partition_key:
                raise QueryError(f"dataset {self.name!r} has no partition key")
            table = table.filter(
                pc.is_in(
                    table[str(partition_key)],
                    value_set=pa.array(list(keys), type=table.schema.field(str(partition_key)).type),
                )
            )
        if normalized_range is not None:
            if not time_field:
                raise QueryError(f"dataset {self.name!r} has no time field")
            start, end = normalized_range
            field = table[str(time_field)]
            table = table.filter(
                pc.and_(
                    pc.greater_equal(field, pa.scalar(start, type=field.type)),
                    pc.less(field, pa.scalar(end, type=field.type)),
                )
            )
        for column, operator, value in filters or ():
            if column not in table.schema.names:
                raise QueryError(f"unknown filter column {column!r}")
            table = table.filter(_filter_mask(table[column], operator, value))
        if order_by:
            sort_keys: list[tuple[str, str]] = []
            for item in order_by:
                column, direction = (item, "ascending") if isinstance(item, str) else item
                if column not in table.schema.names:
                    raise QueryError(f"unknown order column {column!r}")
                normalized_direction = {"asc": "ascending", "desc": "descending"}.get(
                    direction, direction
                )
                if normalized_direction not in {"ascending", "descending"}:
                    raise QueryError(f"invalid order direction {direction!r}")
                sort_keys.append((column, normalized_direction))
            table = table.sort_by(sort_keys)
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise QueryError("limit must be a nonnegative integer")
            table = table.slice(0, limit)
        if columns is not None:
            unknown = [column for column in columns if column not in table.schema.names]
            if unknown:
                raise QueryError(f"unknown projection columns {unknown!r}")
            table = table.select(list(columns))
        return table.combine_chunks()

    def _read_table(self, manifest: Mapping[str, Any], schema: pa.Schema) -> pa.Table:
        snapshot = self.dataset_root / "snapshots" / str(manifest["publication_id"])
        strategy = manifest.get("strategy")
        if strategy == "single-file-csv":
            return pacsv.read_csv(
                snapshot / "data.csv",
                convert_options=pacsv.ConvertOptions(column_types=schema),
            )
        if strategy == "partitioned-parquet":
            files = sorted(snapshot.glob("*/*.parquet"))
            if not files:
                return pa.Table.from_pylist([], schema=schema)
            return pads.dataset(files, schema=schema, format="parquet").to_table()
        raise IncompatibleDatasetError(f"unsupported reader strategy {strategy!r}")

    def _stream_batches_locked(
        self,
        manifest: Mapping[str, Any],
        *,
        batch_size: int,
        keys: Sequence[str] | None,
        time_range: tuple[str | date, str | date] | None,
        columns: Sequence[str] | None,
        filters: Sequence[tuple[str, str, Any]] | None,
        require_coverage: bool,
    ) -> Iterator[pa.RecordBatch]:
        schema = decode_manifest_schema(manifest)
        normalized_range = _normalize_time_range(time_range)
        if require_coverage:
            self._enforce_coverage(manifest, keys=keys, time_range=normalized_range)
        if columns is not None:
            unknown = [column for column in columns if column not in schema.names]
            if unknown:
                raise QueryError(f"unknown projection columns {unknown!r}")
        expression: pads.Expression | None = None
        partition_key = manifest.get("partition_key")
        time_field = manifest.get("time_field")
        if keys is not None:
            if not partition_key:
                raise QueryError(f"dataset {self.name!r} has no partition key")
            expression = pads.field(str(partition_key)).isin(list(keys))
        if normalized_range is not None:
            if not time_field:
                raise QueryError(f"dataset {self.name!r} has no time field")
            start, end = normalized_range
            range_expression = (pads.field(str(time_field)) >= start) & (
                pads.field(str(time_field)) < end
            )
            expression = range_expression if expression is None else expression & range_expression
        for column, operator, value in filters or ():
            if column not in schema.names:
                raise QueryError(f"unknown filter column {column!r}")
            item = _dataset_filter(pads.field(column), operator, value)
            expression = item if expression is None else expression & item

        snapshot = self.dataset_root / "snapshots" / str(manifest["publication_id"])
        strategy = manifest.get("strategy")
        if strategy == "single-file-csv":
            source = pads.dataset(snapshot / "data.csv", schema=schema, format="csv")
        elif strategy == "partitioned-parquet":
            files = sorted(snapshot.glob("*/*.parquet"))
            if not files:
                empty_schema = schema if columns is None else pa.schema(
                    [schema.field(column) for column in columns]
                )
                return iter(pa.RecordBatchReader.from_batches(empty_schema, []))
            source = pads.dataset(files, schema=schema, format="parquet")
        else:
            raise IncompatibleDatasetError(f"unsupported reader strategy {strategy!r}")
        scanner = source.scanner(
            columns=list(columns) if columns is not None else None,
            filter=expression,
            batch_size=batch_size,
        )
        return iter(scanner.to_batches())

    def _read_coverage(self, manifest: Mapping[str, Any]) -> pa.Table:
        if not manifest.get("coverage"):
            raise UnsupportedCoverageError(f"dataset {self.name!r} has no coverage record")
        snapshot = self.dataset_root / "snapshots" / str(manifest["publication_id"])
        return pq.read_table(snapshot / "coverage.parquet", schema=COVERAGE_SCHEMA)

    def _enforce_coverage(
        self,
        manifest: Mapping[str, Any],
        *,
        keys: Sequence[str] | None,
        time_range: tuple[date, date] | None,
    ) -> None:
        if not manifest.get("coverage"):
            raise UnsupportedCoverageError(f"dataset {self.name!r} has no coverage record")
        if keys is None or time_range is None:
            raise QueryError("require_coverage needs explicit keys and time_range")
        coverage = {row["key"]: (row["start"], row["end"]) for row in self._read_coverage(manifest).to_pylist()}
        request_start, request_end = time_range
        missing: dict[str, list[tuple[date, date]]] = {}
        for key in keys:
            item = coverage.get(key)
            gaps: list[tuple[date, date]] = []
            if item is None:
                gaps.append((request_start, request_end))
            else:
                covered_start, covered_end = item
                if request_start < covered_start:
                    gaps.append((request_start, min(request_end, covered_start)))
                if request_end > covered_end:
                    gaps.append((max(request_start, covered_end), request_end))
            gaps = [(start, end) for start, end in gaps if start < end]
            if gaps:
                missing[key] = gaps
        if missing:
            raise CoverageError(self.name, missing)


class BatchReader(AbstractContextManager["BatchReader"]):
    def __init__(self, dataset: DatasetReader, *, batch_size: int, query: Mapping[str, Any]) -> None:
        self.dataset = dataset
        self.batch_size = batch_size
        self.query = dict(query)
        self.publication_id: str | None = None
        self._gate: DatasetGate | None = None
        self._batches: Iterator[pa.RecordBatch] | None = None
        self._limit: int | None = None

    def __enter__(self) -> BatchReader:
        self._gate = DatasetGate(self.dataset.dataset_root / "gate.lock", exclusive=False)
        self._gate.__enter__()
        try:
            manifest = self.dataset._ready_manifest()
            self.publication_id = str(manifest["publication_id"])
            order_by = self.query.get("order_by")
            if order_by:
                table = self.dataset._query_locked(
                    manifest,
                    keys=self.query.get("keys"),
                    time_range=self.query.get("time_range"),
                    columns=self.query.get("columns"),
                    filters=self.query.get("filters"),
                    order_by=order_by,
                    limit=self.query.get("limit"),
                    require_coverage=bool(self.query.get("require_coverage", False)),
                )
                self._batches = iter(table.to_batches(max_chunksize=self.batch_size))
            else:
                self._batches = self.dataset._stream_batches_locked(
                    manifest,
                    batch_size=self.batch_size,
                    keys=self.query.get("keys"),
                    time_range=self.query.get("time_range"),
                    columns=self.query.get("columns"),
                    filters=self.query.get("filters"),
                    require_coverage=bool(self.query.get("require_coverage", False)),
                )
                limit = self.query.get("limit")
                if limit is not None and (not isinstance(limit, int) or limit < 0):
                    raise QueryError("limit must be a nonnegative integer")
                self._limit = limit
            return self
        except BaseException:
            self._gate.__exit__(None, None, None)
            self._gate = None
            raise

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._batches is None:
            raise RuntimeError("batch reader must be used as a context manager")
        if self._limit is None:
            yield from self._batches
            return
        remaining = self._limit
        for batch in self._batches:
            if remaining <= 0:
                break
            emitted = batch.slice(0, min(remaining, batch.num_rows))
            remaining -= emitted.num_rows
            if emitted.num_rows:
                yield emitted

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._batches = None
        self._limit = None
        if self._gate is not None:
            self._gate.__exit__(exc_type, exc, traceback)
            self._gate = None


def _normalize_time_range(
    value: tuple[str | date, str | date] | None,
) -> tuple[date, date] | None:
    if value is None:
        return None
    if len(value) != 2:
        raise QueryError("time_range must contain start and end")
    normalized: list[date] = []
    for item in value:
        try:
            normalized.append(item if isinstance(item, date) else date.fromisoformat(item))
        except (TypeError, ValueError) as exc:
            raise QueryError(f"invalid time_range value {item!r}") from exc
    if normalized[0] >= normalized[1]:
        raise QueryError("time_range must be nonempty and half-open")
    return normalized[0], normalized[1]


def _filter_mask(column: pa.ChunkedArray, operator: str, value: Any) -> pa.Array | pa.ChunkedArray:
    scalar = pa.scalar(value, type=column.type) if operator not in {"in", "not in"} else None
    comparisons = {
        "=": pc.equal,
        "!=": pc.not_equal,
        "<": pc.less,
        "<=": pc.less_equal,
        ">": pc.greater,
        ">=": pc.greater_equal,
    }
    if operator in comparisons:
        return comparisons[operator](column, scalar)
    if operator in {"in", "not in"}:
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise QueryError(f"operator {operator!r} requires a value collection")
        result = pc.is_in(column, value_set=pa.array(list(value), type=column.type))
        return pc.invert(result) if operator == "not in" else result
    raise QueryError(f"unsupported filter operator {operator!r}")


def _dataset_filter(field: pads.Expression, operator: str, value: Any) -> pads.Expression:
    if operator == "=":
        return field == value
    if operator == "!=":
        return field != value
    if operator == "<":
        return field < value
    if operator == "<=":
        return field <= value
    if operator == ">":
        return field > value
    if operator == ">=":
        return field >= value
    if operator in {"in", "not in"}:
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise QueryError(f"operator {operator!r} requires a value collection")
        expression = field.isin(list(value))
        return ~expression if operator == "not in" else expression
    raise QueryError(f"unsupported filter operator {operator!r}")
