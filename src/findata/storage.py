from __future__ import annotations

import base64
import fcntl
import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import duckdb
import pyarrow as pa

from findata.contracts import DatasetSpec


WORKSPACE_VERSION = 1
STORAGE_ADAPTER_VERSION = 1
DATA_LAYOUT_VERSION = 1
DUCKDB_STORAGE_VERSION = "1.5"
DATABASE_NAME = "dataset.duckdb"
FaultInjector = Callable[[str], None]


class StorageError(RuntimeError):
    """Workspace storage cannot satisfy its transactional contract."""


@dataclass(frozen=True, slots=True)
class Coverage:
    key: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("coverage key cannot be empty")
        if self.start >= self.end:
            raise ValueError("coverage interval must be nonempty")


COVERAGE_SCHEMA = pa.schema(
    [
        pa.field("key", pa.string(), nullable=False),
        pa.field("start", pa.date32(), nullable=False),
        pa.field("end", pa.date32(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class DataMutation:
    kind: Literal["complete", "primary_keys", "range"]
    table: pa.Table
    partition: str | None = None
    start: date | None = None
    end: date | None = None

    @classmethod
    def complete(cls, table: pa.Table) -> DataMutation:
        return cls("complete", table)

    @classmethod
    def replace_primary_keys(cls, table: pa.Table) -> DataMutation:
        return cls("primary_keys", table)

    @classmethod
    def replace_range(
        cls,
        table: pa.Table,
        *,
        partition: str,
        start: date,
        end: date,
    ) -> DataMutation:
        if not partition:
            raise ValueError("range mutation partition cannot be empty")
        if start >= end:
            raise ValueError("range mutation must be nonempty")
        return cls("range", table, partition=partition, start=start, end=end)


class DatasetGate(AbstractContextManager["DatasetGate"]):
    def __init__(
        self,
        path: Path,
        *,
        exclusive: bool,
        checkpoint: Callable[[], None] | None = None,
        waiting: Callable[[str], None] | None = None,
        acquired: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self.exclusive = exclusive
        self.checkpoint = checkpoint
        self.waiting = waiting
        self.acquired = acquired
        self._file: Any = None

    def __enter__(self) -> DatasetGate:
        self._file = self.path.open("r+b" if self.exclusive else "rb")
        try:
            if not self.exclusive or self.checkpoint is None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)
            else:
                announced = False
                while True:
                    try:
                        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if self.waiting is not None and not announced:
                            self.waiting("write_gate")
                            announced = True
                        self.checkpoint()
                        time.sleep(0.05)
            if self.acquired is not None:
                self.acquired()
            return self
        except BaseException:
            self._file.close()
            self._file = None
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.datasets_root = self.root / "datasets"

    @classmethod
    def init(cls, root: Path) -> Workspace:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        workspace = cls(root)
        workspace.datasets_root.mkdir(mode=0o700, exist_ok=True)
        marker = root / "workspace.json"
        if not marker.exists():
            _atomic_json(marker, {"workspace_version": WORKSPACE_VERSION}, mode=0o600)
        elif _read_json(marker).get("workspace_version") != WORKSPACE_VERSION:
            raise StorageError("unsupported workspace version")
        config = root / "config.json"
        if not config.exists():
            _atomic_json(
                config,
                {"config_version": 1, "revision": 0, "values": {}},
                mode=0o600,
            )
        else:
            current = _read_json(config)
            if "config_version" not in current:
                values = dict(current.get("values") or {})
                universes = current.get("universes") or {}
                if isinstance(universes, Mapping):
                    for dataset, setting in {
                        "tushare_daily_basic": "update_symbols",
                        "tushare_index_weight": "update_indexes",
                    }.items():
                        if dataset in universes:
                            values[f"dataset.{dataset}.{setting}"] = universes[dataset]
                _atomic_json(
                    config,
                    {"config_version": 1, "revision": 0, "values": values},
                    mode=0o600,
                )
        (root / "config.lock").touch(mode=0o600, exist_ok=True)
        return workspace

    def register_dataset(self, name: str, *, spec: DatasetSpec | None = None) -> None:
        if spec is None:
            raise ValueError("dataset registration requires a declared DatasetSpec")
        if spec.name != name:
            raise ValueError("dataset registration name must match its DatasetSpec")
        dataset_root = dataset_root_path(self.datasets_root, name)
        dataset_root.mkdir(parents=True, exist_ok=True)
        gate_path = dataset_root / "gate.lock"
        gate_path.touch(mode=0o600, exist_ok=True)
        database = dataset_root / DATABASE_NAME
        if database.exists():
            with DatasetGate(gate_path, exclusive=False):
                metadata = load_metadata(database)
            _validate_registration(metadata, spec)
            return
        if (dataset_root / "manifest.json").exists():
            raise StorageError(
                f"dataset {name!r} uses the retired snapshot layout; reset it explicitly"
            )
        temporary = _create_database(dataset_root, spec)
        try:
            with DatasetGate(gate_path, exclusive=True):
                if database.exists():
                    _validate_registration(load_metadata(database), spec)
                    return
                os.replace(temporary, database)
                os.chmod(database, 0o600)
                _fsync_directory(dataset_root)
        finally:
            temporary.unlink(missing_ok=True)

    def reset_dataset(self, name: str, *, spec: DatasetSpec) -> None:
        dataset_root = dataset_root_path(self.datasets_root, name)
        if not dataset_root.is_dir():
            raise StorageError(f"unknown dataset {name!r}")
        temporary = _create_database(dataset_root, spec)
        try:
            with DatasetGate(dataset_root / "gate.lock", exclusive=True):
                current = dataset_root / DATABASE_NAME
                if current.exists():
                    connection = duckdb.connect(str(current))
                    try:
                        connection.execute("checkpoint")
                    finally:
                        connection.close()
                os.replace(temporary, dataset_root / DATABASE_NAME)
                os.chmod(dataset_root / DATABASE_NAME, 0o600)
                _fsync_directory(dataset_root)
        finally:
            temporary.unlink(missing_ok=True)

    def publisher(
        self,
        name: str,
        *,
        fault_injector: FaultInjector | None = None,
        checkpoint: Callable[[], None] | None = None,
        waiting: Callable[[str], None] | None = None,
        acquired: Callable[[], None] | None = None,
    ) -> Publisher:
        return Publisher(
            dataset_root_path(self.datasets_root, name),
            fault_injector=fault_injector,
            checkpoint=checkpoint,
            waiting=waiting,
            acquired=acquired,
        )

    def set_config(self, key: str, value: Any) -> None:
        if not key or key in {"universes", "workspace_version"}:
            raise ValueError("invalid configuration key")
        with DatasetGate(self.root / "config.lock", exclusive=True):
            config = _read_json(self.root / "config.json")
            values = dict(config.get("values") or {})
            values[key] = value
            config["values"] = values
            config["revision"] = int(config.get("revision", 0)) + 1
            _atomic_json(self.root / "config.json", config, mode=0o600)

    def config_snapshot(self) -> dict[str, Any]:
        with DatasetGate(self.root / "config.lock", exclusive=False):
            config = _read_json(self.root / "config.json")
            return {
                "config_version": int(config.get("config_version", 1)),
                "revision": int(config.get("revision", 0)),
                "values": dict(config.get("values") or {}),
            }

    def get_config(self, key: str, default: Any = None) -> Any:
        with DatasetGate(self.root / "config.lock", exclusive=False):
            config = _read_json(self.root / "config.json")
            values = config.get("values") or {}
            return values.get(key, default) if isinstance(values, Mapping) else default

    def list_config(self) -> dict[str, Any]:
        with DatasetGate(self.root / "config.lock", exclusive=False):
            config = _read_json(self.root / "config.json")
            values = config.get("values") or {}
            return dict(values) if isinstance(values, Mapping) else {}

    def unset_config(self, key: str) -> bool:
        with DatasetGate(self.root / "config.lock", exclusive=True):
            config = _read_json(self.root / "config.json")
            values = dict(config.get("values") or {})
            existed = key in values
            values.pop(key, None)
            config["values"] = values
            config["revision"] = int(config.get("revision", 0)) + 1
            _atomic_json(self.root / "config.json", config, mode=0o600)
            return existed

    def recover_storage(self) -> int:
        removed = 0
        if not self.datasets_root.exists():
            return removed
        for dataset_root in self.datasets_root.iterdir():
            database = dataset_root / DATABASE_NAME
            if not dataset_root.is_dir() or not database.is_file():
                continue
            with DatasetGate(dataset_root / "gate.lock", exclusive=True):
                for path in dataset_root.glob(".findata-input-*"):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                        removed += 1
                connection = duckdb.connect(str(database))
                try:
                    connection.execute("checkpoint")
                finally:
                    connection.close()
        return removed


class Publisher:
    def __init__(
        self,
        dataset_root: Path,
        *,
        fault_injector: FaultInjector | None = None,
        checkpoint: Callable[[], None] | None = None,
        waiting: Callable[[str], None] | None = None,
        acquired: Callable[[], None] | None = None,
    ) -> None:
        self.dataset_root = dataset_root
        self._fault = fault_injector or (lambda _point: None)
        self._checkpoint = checkpoint
        self._waiting = waiting
        self._acquired = acquired

    def publish(self, table: pa.Table, *, coverage: Iterable[Coverage] | None = None) -> str:
        return self.commit([DataMutation.complete(table)], coverage=coverage)

    def commit(
        self,
        mutations: Sequence[DataMutation],
        *,
        coverage: Iterable[Coverage] | None = None,
    ) -> str:
        coverage_rows = None if coverage is None else list(coverage)
        if coverage_rows is not None:
            _validate_coverage(coverage_rows)
        publication_id = uuid.uuid4().hex
        if not (self.dataset_root / DATABASE_NAME).is_file():
            raise StorageError(f"unknown dataset {self.dataset_root.name!r}")
        if not (self.dataset_root / "gate.lock").is_file():
            raise StorageError(f"dataset {self.dataset_root.name!r} is missing its storage gate")
        self._fault("before_gate")
        with DatasetGate(
            self.dataset_root / "gate.lock",
            exclusive=True,
            checkpoint=self._checkpoint,
            waiting=self._waiting,
            acquired=self._acquired,
        ):
            self._fault("after_gate")
            connection = duckdb.connect(str(self.dataset_root / DATABASE_NAME))
            committed = False
            try:
                metadata = _metadata_from_row(_metadata_row(connection))
                schema = decode_schema(str(metadata["schema"]))
                if metadata["time_field"] is not None and coverage_rows is None:
                    raise StorageError("coverage-tracked dataset commit requires coverage")
                if metadata["time_field"] is None and coverage_rows:
                    raise StorageError("non-coverage dataset cannot commit coverage")
                for mutation in mutations:
                    _validate_mutation(mutation, metadata, schema)
                self._fault("before_transaction")
                connection.execute("begin transaction")
                for index, mutation in enumerate(mutations):
                    _apply_mutation(connection, mutation, metadata, index=index)
                self._fault("after_data_mutation")
                if coverage_rows is not None:
                    _replace_coverage(connection, coverage_rows)
                self._fault("after_coverage_mutation")
                _validate_primary_keys(connection, metadata)
                connection.execute(
                    """
                    update _findata_metadata
                    set state = 'ready', revision = revision + 1, publication_id = ?
                    """,
                    [publication_id],
                )
                self._fault("after_metadata_mutation")
                self._fault("before_commit")
                connection.execute("commit")
                committed = True
                self._fault("after_commit")
                return publication_id
            except BaseException:
                if not committed:
                    try:
                        connection.execute("rollback")
                    except duckdb.Error:
                        pass
                raise
            finally:
                connection.close()


def load_metadata(database: Path) -> dict[str, Any]:
    try:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            return _metadata_from_row(_metadata_row(connection))
        finally:
            connection.close()
    except duckdb.Error as exc:
        raise StorageError(f"cannot read {database.name}") from exc


def read_metadata(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return _metadata_from_row(_metadata_row(connection))


def decode_schema(value: str) -> pa.Schema:
    try:
        return pa.ipc.read_schema(pa.BufferReader(base64.b64decode(value)))
    except (ValueError, pa.ArrowException) as exc:
        raise StorageError("database metadata contains an invalid Arrow schema") from exc


def dataset_root_path(datasets_root: Path, name: str) -> Path:
    """Resolve one dataset directory without permitting traversal outside its owner root."""
    value = str(name)
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"invalid dataset name {value!r}")
    return Path(datasets_root) / value


def _create_database(dataset_root: Path, spec: DatasetSpec) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".dataset.duckdb.", dir=dataset_root)
    os.close(descriptor)
    temporary = Path(name)
    temporary.unlink()
    connection = duckdb.connect(str(temporary))
    try:
        empty = pa.Table.from_batches([], schema=spec.schema)
        connection.register("_findata_schema_input", empty)
        connection.execute("create table data as select * from _findata_schema_input limit 0")
        connection.unregister("_findata_schema_input")
        connection.execute(
            """
            create table _findata_coverage (
                key varchar not null,
                start date not null,
                "end" date not null
            )
            """
        )
        connection.execute(
            """
            create table _findata_metadata (
                dataset varchar not null,
                storage_adapter_version integer not null,
                duckdb_storage_version varchar not null,
                data_layout_version integer not null,
                schema varchar not null,
                primary_key varchar not null,
                partition_key varchar,
                secondary_key varchar,
                time_field varchar,
                missing_data_policy varchar not null,
                state varchar not null,
                revision ubigint not null,
                publication_id varchar
            )
            """
        )
        connection.execute(
            """
            insert into _findata_metadata values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uninitialized', 0, null)
            """,
            [
                spec.name,
                STORAGE_ADAPTER_VERSION,
                DUCKDB_STORAGE_VERSION,
                DATA_LAYOUT_VERSION,
                _encode_schema(spec.schema),
                json.dumps(list(spec.primary_key), separators=(",", ":")),
                spec.partition_key,
                spec.secondary_key,
                spec.time_field,
                spec.missing_data_policy,
            ],
        )
        connection.execute("checkpoint")
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    os.chmod(temporary, 0o600)
    return temporary


def _metadata_row(connection: duckdb.DuckDBPyConnection) -> tuple[Any, ...]:
    rows = connection.execute(
        """
        select dataset, storage_adapter_version, duckdb_storage_version,
               data_layout_version, schema, primary_key, partition_key,
               secondary_key, time_field, missing_data_policy, state, revision, publication_id
        from _findata_metadata
        """
    ).fetchall()
    if len(rows) != 1:
        raise StorageError("dataset database must contain exactly one metadata row")
    return rows[0]


def _metadata_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    names = (
        "dataset",
        "storage_adapter_version",
        "duckdb_storage_version",
        "data_layout_version",
        "schema",
        "primary_key",
        "partition_key",
        "secondary_key",
        "time_field",
        "missing_data_policy",
        "state",
        "revision",
        "publication_id",
    )
    result = dict(zip(names, row, strict=True))
    try:
        result["primary_key"] = tuple(json.loads(str(result["primary_key"])))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError("database metadata contains invalid primary keys") from exc
    return result


def _validate_registration(metadata: Mapping[str, Any], spec: DatasetSpec) -> None:
    expected = {
        "dataset": spec.name,
        "storage_adapter_version": STORAGE_ADAPTER_VERSION,
        "duckdb_storage_version": DUCKDB_STORAGE_VERSION,
        "data_layout_version": DATA_LAYOUT_VERSION,
        "schema": _encode_schema(spec.schema),
        "primary_key": tuple(spec.primary_key),
        "partition_key": spec.partition_key,
        "secondary_key": spec.secondary_key,
        "time_field": spec.time_field,
        "missing_data_policy": spec.missing_data_policy,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise StorageError(f"dataset {spec.name!r} is already registered incompatibly")


def _validate_mutation(
    mutation: DataMutation,
    metadata: Mapping[str, Any],
    schema: pa.Schema,
) -> None:
    if not mutation.table.schema.equals(schema, check_metadata=False):
        raise StorageError("mutation table does not match registered logical schema")
    if mutation.kind == "range":
        partition_key = metadata.get("partition_key")
        time_field = metadata.get("time_field")
        if not partition_key or not time_field:
            raise StorageError("range mutation requires registered partition and time fields")
        assert (
            mutation.partition is not None
            and mutation.start is not None
            and mutation.end is not None
        )
        for row in mutation.table.select([partition_key, time_field]).to_pylist():
            if row[partition_key] != mutation.partition:
                raise StorageError("range mutation contains a row for another partition")
            if not mutation.start <= row[time_field] < mutation.end:
                raise StorageError("range mutation contains a row outside its declared interval")


def _apply_mutation(
    connection: duckdb.DuckDBPyConnection,
    mutation: DataMutation,
    metadata: Mapping[str, Any],
    *,
    index: int,
) -> None:
    relation = f"_findata_input_{index}"
    connection.register(relation, mutation.table)
    quoted_relation = _quote_identifier(relation)
    try:
        if mutation.kind == "complete":
            connection.execute("delete from data")
        elif mutation.kind == "primary_keys":
            keys = tuple(metadata["primary_key"])
            if mutation.table.num_rows:
                predicate = " and ".join(
                    f"data.{_quote_identifier(key)} = incoming.{_quote_identifier(key)}"
                    for key in keys
                )
                connection.execute(
                    f"delete from data where exists "
                    f"(select 1 from {quoted_relation} incoming where {predicate})"
                )
        elif mutation.kind == "range":
            partition_key = _quote_identifier(str(metadata["partition_key"]))
            time_field = _quote_identifier(str(metadata["time_field"]))
            connection.execute(
                f"delete from data where {partition_key} = ? and {time_field} >= ? and {time_field} < ?",
                [mutation.partition, mutation.start, mutation.end],
            )
        else:
            raise StorageError(f"unsupported mutation kind {mutation.kind!r}")
        if mutation.table.num_rows:
            connection.execute(f"insert into data select * from {quoted_relation}")
    finally:
        connection.unregister(relation)


def _replace_coverage(
    connection: duckdb.DuckDBPyConnection,
    entries: Sequence[Coverage],
) -> None:
    connection.execute("delete from _findata_coverage")
    if not entries:
        return
    table = pa.Table.from_pylist(
        [{"key": item.key, "start": item.start, "end": item.end} for item in entries],
        schema=COVERAGE_SCHEMA,
    )
    connection.register("_findata_coverage_input", table)
    try:
        connection.execute(
            'insert into _findata_coverage select key, start, "end" from _findata_coverage_input'
        )
    finally:
        connection.unregister("_findata_coverage_input")


def _validate_primary_keys(
    connection: duckdb.DuckDBPyConnection,
    metadata: Mapping[str, Any],
) -> None:
    keys = tuple(metadata["primary_key"])
    columns = ", ".join(_quote_identifier(key) for key in keys)
    nulls = " or ".join(f"{_quote_identifier(key)} is null" for key in keys)
    if connection.execute(f"select count(*) from data where {nulls}").fetchone()[0]:
        raise StorageError("logical primary-key fields cannot be null")
    duplicate = connection.execute(
        f"select 1 from data group by {columns} having count(*) > 1 limit 1"
    ).fetchone()
    if duplicate is not None:
        raise StorageError("logical primary-key tuples must be unique")


def _validate_coverage(entries: Iterable[Coverage]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.key in seen:
            raise StorageError(f"duplicate coverage key {entry.key!r}")
        seen.add(entry.key)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _encode_schema(schema: pa.Schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"cannot read {path.name}") from exc
    if not isinstance(result, dict):
        raise StorageError(f"{path.name} must contain an object")
    return result


def _atomic_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
