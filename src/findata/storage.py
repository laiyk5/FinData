from __future__ import annotations

import base64
import fcntl
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from findata.datasets.tushare import TUSHARE_DATASETS


WORKSPACE_VERSION = 1
MANIFEST_VERSION = 1
FaultInjector = Callable[[str], None]


class StorageError(RuntimeError):
    """Workspace storage cannot satisfy its publication contract."""


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


class DatasetGate(AbstractContextManager["DatasetGate"]):
    def __init__(self, path: Path, *, exclusive: bool) -> None:
        self.path = path
        self.exclusive = exclusive
        self._file: Any = None

    def __enter__(self) -> DatasetGate:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)
        return self

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
        else:
            data = _read_json(marker)
            if data.get("workspace_version") != WORKSPACE_VERSION:
                raise StorageError("unsupported workspace version")
        config = root / "config.json"
        if not config.exists():
            _atomic_json(config, {"universes": {}}, mode=0o600)
        (root / "config.lock").touch(mode=0o600, exist_ok=True)
        return workspace

    def register_dataset(self, name: str, *, strategy: str) -> None:
        if strategy not in {"single-file-csv", "partitioned-parquet"}:
            raise ValueError(f"unsupported storage strategy: {strategy}")
        spec = TUSHARE_DATASETS[name]
        dataset_root = self.datasets_root / name
        dataset_root.mkdir(parents=True, exist_ok=True)
        (dataset_root / "snapshots").mkdir(exist_ok=True)
        (dataset_root / "staging").mkdir(exist_ok=True)
        (dataset_root / "gate.lock").touch(mode=0o600, exist_ok=True)
        manifest_path = dataset_root / "manifest.json"
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "data_layout_version": 1,
            "dataset": name,
            "state": "uninitialized",
            "publication_id": None,
            "schema": _encode_schema(spec.schema),
            "primary_key": list(spec.primary_key),
            "partition_key": spec.partition_key,
            "secondary_key": spec.secondary_key,
            "time_field": spec.time_field,
            "strategy": strategy,
            "coverage": False,
        }
        if manifest_path.exists():
            existing = _read_json(manifest_path)
            immutable = ("dataset", "schema", "primary_key", "partition_key", "time_field", "strategy")
            if any(existing.get(key) != manifest.get(key) for key in immutable):
                raise StorageError(f"dataset {name!r} is already registered incompatibly")
            return
        _atomic_json(manifest_path, manifest)

    def publisher(
        self,
        name: str,
        *,
        fault_injector: FaultInjector | None = None,
    ) -> Publisher:
        return Publisher(self.datasets_root / name, fault_injector=fault_injector)

    def set_universe(self, dataset: str, selectors: Iterable[str]) -> None:
        values = list(dict.fromkeys(selectors))
        if not values or any(not isinstance(value, str) or not value for value in values):
            raise ValueError("maintenance universe must contain nonempty selectors")
        with DatasetGate(self.root / "config.lock", exclusive=True):
            config = _read_json(self.root / "config.json")
            universes = dict(config.get("universes") or {})
            universes[dataset] = values
            config["universes"] = universes
            _atomic_json(self.root / "config.json", config, mode=0o600)

    def get_universe(self, dataset: str) -> list[str]:
        with DatasetGate(self.root / "config.lock", exclusive=False):
            config = _read_json(self.root / "config.json")
            universes = config.get("universes") or {}
            values = universes.get(dataset, []) if isinstance(universes, Mapping) else []
            return list(values) if isinstance(values, list) else []

    def set_config(self, key: str, value: Any) -> None:
        if not key or key in {"universes", "workspace_version"}:
            raise ValueError("invalid configuration key")
        with DatasetGate(self.root / "config.lock", exclusive=True):
            config = _read_json(self.root / "config.json")
            values = dict(config.get("values") or {})
            values[key] = value
            config["values"] = values
            _atomic_json(self.root / "config.json", config, mode=0o600)

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
            _atomic_json(self.root / "config.json", config, mode=0o600)
            return existed


class Publisher:
    def __init__(self, dataset_root: Path, *, fault_injector: FaultInjector | None = None) -> None:
        self.dataset_root = dataset_root
        self._fault = fault_injector or (lambda _point: None)

    def publish(self, table: pa.Table, *, coverage: Iterable[Coverage] | None = None) -> str:
        manifest_path = self.dataset_root / "manifest.json"
        manifest = _read_json(manifest_path)
        schema = _decode_schema(str(manifest["schema"]))
        if not table.schema.equals(schema, check_metadata=False):
            raise StorageError("published table does not match registered logical schema")
        coverage_rows = list(coverage or ())
        if manifest.get("time_field") is None and coverage_rows:
            raise StorageError("snapshot dataset cannot publish coverage")
        if manifest.get("time_field") is not None and not coverage_rows:
            raise StorageError("coverage-tracked dataset publication requires coverage")
        _validate_coverage(coverage_rows)

        publication_id = uuid.uuid4().hex
        staging = Path(tempfile.mkdtemp(prefix=f"{publication_id}-", dir=self.dataset_root / "staging"))
        installed = False
        try:
            self._fault("after_staging_created")
            strategy = str(manifest["strategy"])
            if strategy == "single-file-csv":
                pacsv.write_csv(table, staging / "data.csv")
            elif strategy == "partitioned-parquet":
                _write_partitioned(table, manifest, staging)
            else:
                raise StorageError(f"unsupported storage strategy: {strategy}")
            if coverage_rows:
                coverage_table = pa.Table.from_pylist(
                    [
                        {"key": item.key, "start": item.start, "end": item.end}
                        for item in coverage_rows
                    ],
                    schema=COVERAGE_SCHEMA,
                )
                pq.write_table(coverage_table, staging / "coverage.parquet")
            _fsync_tree(staging)
            self._fault("after_snapshot_flush")

            with DatasetGate(self.dataset_root / "gate.lock", exclusive=True):
                destination = self.dataset_root / "snapshots" / publication_id
                os.replace(staging, destination)
                installed = True
                _fsync_directory(destination.parent)
                self._fault("before_manifest_commit")
                next_manifest = dict(manifest)
                next_manifest.update(
                    {
                        "state": "ready",
                        "publication_id": publication_id,
                        "coverage": bool(coverage_rows),
                    }
                )
                _atomic_json(manifest_path, next_manifest)
                self._fault("after_manifest_commit")
            return publication_id
        except BaseException:
            if not installed:
                shutil.rmtree(staging, ignore_errors=True)
            raise


def load_manifest(dataset_root: Path) -> dict[str, Any]:
    return _read_json(dataset_root / "manifest.json")


def decode_manifest_schema(manifest: Mapping[str, Any]) -> pa.Schema:
    return _decode_schema(str(manifest["schema"]))


def _write_partitioned(table: pa.Table, manifest: Mapping[str, Any], root: Path) -> None:
    partition_key = manifest.get("partition_key")
    time_field = manifest.get("time_field")
    if not partition_key or not time_field:
        raise StorageError("partitioned strategy requires partition and time fields")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in table.to_pylist():
        key = row.get(str(partition_key))
        timestamp = row.get(str(time_field))
        if not isinstance(key, str) or not isinstance(timestamp, date):
            raise StorageError("partition key and time field must be normalized")
        month = timestamp.strftime("%Y%m")
        groups.setdefault((key, month), []).append(row)
    for (key, month), rows in groups.items():
        key_dir = root / quote(key, safe="._-")
        key_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), key_dir / f"{month}.parquet")


def _validate_coverage(entries: Iterable[Coverage]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.key in seen:
            raise StorageError(f"duplicate coverage key {entry.key!r}")
        seen.add(entry.key)


def _encode_schema(schema: pa.Schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def _decode_schema(value: str) -> pa.Schema:
    try:
        return pa.ipc.read_schema(pa.BufferReader(base64.b64decode(value)))
    except (ValueError, pa.ArrowException) as exc:
        raise StorageError("manifest contains an invalid Arrow schema") from exc


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


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as file:
                os.fsync(file.fileno())
    directories = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
    for directory in directories:
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
