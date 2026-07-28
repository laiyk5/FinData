from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import date
import re
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from findata.storage import (
    COVERAGE_SCHEMA,
    DATA_LAYOUT_VERSION,
    DATABASE_NAME,
    DUCKDB_STORAGE_VERSION,
    STORAGE_ADAPTER_VERSION,
    DatasetGate,
    decode_schema,
    dataset_root_path,
    read_metadata,
)


class DataLoaderError(RuntimeError):
    pass


class DatasetNotReadyError(DataLoaderError):
    pass


class DatasetNotFoundError(DataLoaderError):
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
        rendered = "; ".join(
            f"{key} {', '.join(f'{start.isoformat()}:{end.isoformat()}' for start, end in intervals)}"
            for key, intervals in self.missing_intervals.items()
        )
        super().__init__(f"{dataset} has unresolved coverage: {rendered}")


class DataLoader:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def dataset(self, name: str) -> DatasetReader:
        dataset_root = dataset_root_path(self.workspace / "datasets", name)
        return DatasetReader(dataset_root, name=name)

    def list_datasets(self) -> list[str]:
        """Return all dataset names registered in this workspace, in alphabetical order.

        Only datasets that have been physically created (i.e., have a database file) are
        returned; a plugin that was discovered but never initialized is not included.
        """
        datasets_dir = self.workspace / "datasets"
        if not datasets_dir.is_dir():
            return []
        names: list[str] = []
        for db in datasets_dir.rglob(DATABASE_NAME):
            if db.is_file():
                rel = db.parent.relative_to(datasets_dir)
                names.append(str(rel))
        names.sort()
        return names


class DatasetReader:
    def __init__(self, dataset_root: Path, *, name: str) -> None:
        self.dataset_root = dataset_root
        self.name = name

    @property
    def publication_id(self) -> str:
        with self._shared_gate():
            connection = self._connect()
            try:
                metadata = self._ready_metadata(connection)
                return str(metadata["publication_id"])
            finally:
                connection.close()

    def describe(self) -> dict[str, Any]:
        """Return storage-neutral schema and key metadata for one committed revision."""
        with self._shared_gate():
            connection = self._connect()
            try:
                metadata = self._ready_metadata(connection)
                schema = decode_schema(str(metadata["schema"]))
                return {
                    "dataset": self.name,
                    "publication_id": str(metadata["publication_id"]),
                    "fields": [
                        {
                            "name": field.name,
                            "type": str(field.type),
                            "nullable": field.nullable,
                        }
                        for field in schema
                    ],
                    "primary_key": list(metadata["primary_key"]),
                    "partition_key": metadata.get("partition_key"),
                    "time_field": metadata.get("time_field"),
                    "coverage_supported": bool(metadata.get("time_field")),
                }
            finally:
                connection.close()

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
        with self._shared_gate():
            connection = self._connect()
            try:
                metadata = self._ready_metadata(connection)
                return self._query_locked(
                    connection,
                    metadata,
                    keys=keys,
                    time_range=time_range,
                    columns=columns,
                    filters=filters,
                    order_by=order_by,
                    limit=limit,
                    require_coverage=require_coverage,
                )
            finally:
                connection.close()

    def query_sql(self, sql: str, *, limit: int | None = None) -> pa.Table:
        """Run one guarded read-only SQL query against this dataset's ``data`` relation.

        The query executes through the same locked, read-only DataLoader connection
        as :meth:`query`. It accepts one ``SELECT`` statement whose only source
        relation is the dataset's public ``data`` table.
        """
        statement = _guard_dataset_sql(sql)
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= 1_000:
                raise QueryError("limit must be an integer between 1 and 1000")
            statement = f"select * from ({statement}) as findata_query limit ?"
            parameters: list[Any] = [limit]
        else:
            parameters = []
        with self._shared_gate():
            connection = self._connect()
            try:
                self._ready_metadata(connection)
                return connection.execute(statement, parameters).to_arrow_table().combine_chunks()
            except duckdb.Error as exc:
                raise QueryError(f"dataset SQL query failed: {exc}") from exc
            finally:
                connection.close()

    def iter_batches(self, *, batch_size: int = 65_536, **query: Any) -> BatchReader:
        if batch_size <= 0:
            raise QueryError("batch_size must be positive")
        return BatchReader(self, batch_size=batch_size, query=query)

    def coverage(self, keys: Sequence[str] | None = None) -> pa.Table:
        with self._shared_gate():
            connection = self._connect()
            try:
                metadata = self._ready_metadata(connection)
                return self._read_coverage(connection, metadata, keys=keys)
            finally:
                connection.close()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        database = self.dataset_root / DATABASE_NAME
        try:
            return duckdb.connect(str(database), read_only=True)
        except duckdb.Error as exc:
            # A conflicting-lock failure here means another process opened the
            # database outside the gate protocol. Do not retry: this error is
            # the detector for that protocol violation.
            raise DataLoaderError(f"cannot load dataset {self.name!r}") from exc

    def _shared_gate(self) -> DatasetGate:
        if not (self.dataset_root / DATABASE_NAME).is_file():
            raise DatasetNotFoundError(f"unknown dataset {self.name!r}")
        gate = self.dataset_root / "gate.lock"
        if not gate.is_file():
            raise DataLoaderError(f"dataset {self.name!r} is missing its storage gate")
        return DatasetGate(gate, exclusive=False)

    def _ready_metadata(self, connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        try:
            metadata = read_metadata(connection)
        except Exception as exc:
            raise DataLoaderError(f"cannot load dataset {self.name!r}") from exc
        versions = (
            ("storage adapter", metadata.get("storage_adapter_version"), STORAGE_ADAPTER_VERSION),
            ("DuckDB storage", metadata.get("duckdb_storage_version"), DUCKDB_STORAGE_VERSION),
            ("data layout", metadata.get("data_layout_version"), DATA_LAYOUT_VERSION),
        )
        for label, actual, expected in versions:
            if actual != expected:
                raise IncompatibleDatasetError(
                    f"dataset {self.name!r} uses {label} version {actual!r}; expected {expected!r}"
                )
        if metadata.get("dataset") != self.name:
            raise IncompatibleDatasetError(
                f"dataset database identifies itself as {metadata.get('dataset')!r}"
            )
        if metadata.get("state") != "ready" or not metadata.get("publication_id"):
            raise DatasetNotReadyError(f"dataset {self.name!r} has no committed revision")
        return metadata

    def _query_locked(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: Mapping[str, Any],
        *,
        keys: Sequence[str] | None,
        time_range: tuple[str | date, str | date] | None,
        columns: Sequence[str] | None,
        filters: Sequence[tuple[str, str, Any]] | None,
        order_by: Sequence[str | tuple[str, str]] | None,
        limit: int | None,
        require_coverage: bool,
    ) -> pa.Table:
        sql, parameters = self._compile_query(
            connection,
            metadata,
            keys=keys,
            time_range=time_range,
            columns=columns,
            filters=filters,
            order_by=order_by,
            limit=limit,
            require_coverage=require_coverage,
        )
        try:
            return connection.execute(sql, parameters).to_arrow_table().combine_chunks()
        except duckdb.Error as exc:
            raise QueryError(f"dataset query failed: {exc}") from exc

    def _compile_query(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: Mapping[str, Any],
        *,
        keys: Sequence[str] | None,
        time_range: tuple[str | date, str | date] | None,
        columns: Sequence[str] | None,
        filters: Sequence[tuple[str, str, Any]] | None,
        order_by: Sequence[str | tuple[str, str]] | None,
        limit: int | None,
        require_coverage: bool,
    ) -> tuple[str, list[Any]]:
        schema = decode_schema(str(metadata["schema"]))
        known = set(schema.names)
        normalized_range = _normalize_time_range(time_range)
        if require_coverage:
            self._enforce_coverage(
                connection,
                metadata,
                keys=keys,
                time_range=normalized_range,
            )
        selected = list(columns) if columns is not None else list(schema.names)
        unknown = [column for column in selected if column not in known]
        if unknown:
            raise QueryError(f"unknown projection columns {unknown!r}")
        clauses: list[str] = []
        parameters: list[Any] = []
        partition_key = metadata.get("partition_key")
        time_field = metadata.get("time_field")
        if keys is not None:
            if not partition_key:
                raise QueryError(f"dataset {self.name!r} has no partition key")
            values = list(keys)
            clauses.append(_in_clause(_quote_identifier(str(partition_key)), values, negate=False))
            parameters.extend(values)
        if normalized_range is not None:
            if not time_field:
                raise QueryError(f"dataset {self.name!r} has no time field")
            start, end = normalized_range
            field = _quote_identifier(str(time_field))
            clauses.append(f"{field} >= ? and {field} < ?")
            parameters.extend([start, end])
        for column, operator, value in filters or ():
            if column not in known:
                raise QueryError(f"unknown filter column {column!r}")
            field = _quote_identifier(column)
            if operator in {"=", "!=", "<", "<=", ">", ">="}:
                clauses.append(f"{field} {operator} ?")
                parameters.append(value)
            elif operator in {"in", "not in"}:
                if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
                    raise QueryError(f"operator {operator!r} requires a value collection")
                values = list(value)
                clauses.append(_in_clause(field, values, negate=operator == "not in"))
                parameters.extend(values)
            else:
                raise QueryError(f"unsupported filter operator {operator!r}")
        sql = "select " + ", ".join(_quote_identifier(column) for column in selected) + " from data"
        if clauses:
            sql += " where " + " and ".join(f"({clause})" for clause in clauses)
        if order_by:
            terms: list[str] = []
            for item in order_by:
                column, direction = (item, "ascending") if isinstance(item, str) else item
                if column not in known:
                    raise QueryError(f"unknown order column {column!r}")
                normalized = {"asc": "ascending", "desc": "descending"}.get(direction, direction)
                if normalized not in {"ascending", "descending"}:
                    raise QueryError(f"invalid order direction {direction!r}")
                terms.append(
                    f"{_quote_identifier(column)} {'asc' if normalized == 'ascending' else 'desc'}"
                )
            sql += " order by " + ", ".join(terms)
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise QueryError("limit must be a nonnegative integer")
            sql += " limit ?"
            parameters.append(limit)
        return sql, parameters

    def _read_coverage(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: Mapping[str, Any],
        *,
        keys: Sequence[str] | None = None,
    ) -> pa.Table:
        if not metadata.get("time_field"):
            raise UnsupportedCoverageError(f"dataset {self.name!r} has no coverage record")
        sql = 'select key, start, "end" from _findata_coverage'
        parameters: list[Any] = []
        if keys is not None:
            values = list(keys)
            sql += " where " + _in_clause("key", values, negate=False)
            parameters.extend(values)
        sql += " order by key"
        table = connection.execute(sql, parameters).to_arrow_table()
        return table.cast(COVERAGE_SCHEMA).combine_chunks()

    def _enforce_coverage(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: Mapping[str, Any],
        *,
        keys: Sequence[str] | None,
        time_range: tuple[date, date] | None,
    ) -> None:
        if not metadata.get("time_field"):
            raise UnsupportedCoverageError(f"dataset {self.name!r} has no coverage record")
        if keys is None or time_range is None:
            raise QueryError("require_coverage needs explicit keys and time_range")
        coverage = {
            row["key"]: (row["start"], row["end"])
            for row in self._read_coverage(connection, metadata, keys=keys).to_pylist()
        }
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
    def __init__(
        self, dataset: DatasetReader, *, batch_size: int, query: Mapping[str, Any]
    ) -> None:
        self.dataset = dataset
        self.batch_size = batch_size
        self.query = dict(query)
        self.publication_id: str | None = None
        self._gate: DatasetGate | None = None
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._reader: pa.RecordBatchReader | None = None

    def __enter__(self) -> BatchReader:
        self._gate = self.dataset._shared_gate()
        self._gate.__enter__()
        try:
            self._connection = self.dataset._connect()
            metadata = self.dataset._ready_metadata(self._connection)
            self.publication_id = str(metadata["publication_id"])
            sql, parameters = self.dataset._compile_query(
                self._connection,
                metadata,
                keys=self.query.get("keys"),
                time_range=self.query.get("time_range"),
                columns=self.query.get("columns"),
                filters=self.query.get("filters"),
                order_by=self.query.get("order_by"),
                limit=self.query.get("limit"),
                require_coverage=bool(self.query.get("require_coverage", False)),
            )
            self._reader = self._connection.execute(sql, parameters).to_arrow_reader(
                self.batch_size
            )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._reader is None:
            raise RuntimeError("batch reader must be used as a context manager")
        yield from self._reader

    @property
    def schema(self) -> pa.Schema:
        if self._reader is None:
            raise RuntimeError("batch reader must be used as a context manager")
        return self._reader.schema

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._reader = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
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


def _in_clause(field: str, values: Sequence[Any], *, negate: bool) -> str:
    if not values:
        return "true" if negate else "false"
    operator = "not in" if negate else "in"
    return f"{field} {operator} ({', '.join('?' for _ in values)})"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _guard_dataset_sql(sql: str) -> str:
    """Permit one read-only SELECT over the public ``data`` relation only."""
    if not isinstance(sql, str) or not sql.strip():
        raise QueryError("SQL query must be a nonempty string")
    statement = sql.strip()
    normalized = statement.lower()
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise QueryError("SQL query must contain one statement without comments")
    if not normalized.startswith("select"):
        raise QueryError("SQL query must start with SELECT")
    if len(re.findall(r"\bselect\b", normalized)) != 1:
        raise QueryError("SQL subqueries are not supported")
    if re.search(r"\b(join|union|intersect|except)\b", normalized):
        raise QueryError("SQL joins and set operations are not supported")
    if re.search(
        r"\b(read_[a-z_]*|parquet_scan|csv_scan|sqlite_scan|postgres_scan|httpfs|glob|query_table|pragma_[a-z_]*)\b",
        normalized,
    ):
        raise QueryError("SQL query may only read the dataset data relation")
    source = re.search(r'\bfrom\s+("data"|data)\b', normalized)
    if source is None:
        raise QueryError("SQL query must read from the dataset data relation")
    if re.match(r"\s*,", normalized[source.end() :]):
        raise QueryError("SQL query may only use one source relation")
    return statement
