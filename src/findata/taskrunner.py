from __future__ import annotations

import json
import multiprocessing as mp
import os
import secrets
import signal
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"succeeded", "failed", "canceled"}
ACTIVE_STATES = {"queued", "running", "waiting", "canceling"}
COALESCING_OPERATIONS = {"complete", "refresh"}


class TaskRunnerError(RuntimeError):
    pass


class QueueFullError(TaskRunnerError):
    pass


class TaskNotFoundError(TaskRunnerError):
    pass


class TaskCancelled(Exception):
    pass


@dataclass(slots=True)
class HandleRecord:
    handle_id: str
    execution_id: str
    dataset: str
    operation: str
    owner: str
    status: str
    created_at: float
    updated_at: float
    result: Any = None
    error: str | None = None
    progress: dict[str, int | float] | None = None


@dataclass(slots=True)
class ExecutionRecord:
    execution_id: str
    dataset: str
    operation: str
    operands: dict[str, Any]
    status: str
    created_at: float
    updated_at: float
    coalescing_key: str | None
    handle_ids: list[str] = field(default_factory=list)
    pid: int | None = None
    process_start: str | None = None
    result: Any = None
    error: str | None = None
    progress: dict[str, int | float] | None = None
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class CancellationResult:
    handle_id: str
    shared_execution_continues: bool
    status: str


class TaskContext:
    """Child-process interface for logs, progress, states, and cooperative cancellation."""

    def __init__(self, connection: Connection, canceled: threading.Event) -> None:
        self._connection = connection
        self._canceled = canceled
        self._send_lock = threading.Lock()

    def checkpoint(self) -> None:
        if self._canceled.is_set():
            raise TaskCancelled()

    def log(self, message: str) -> None:
        self._send({"type": "log", "message": str(message)})

    def progress(self, current: int | float, total: int | float) -> None:
        if total < 0 or current < 0:
            raise ValueError("progress values cannot be negative")
        self._send({"type": "progress", "current": current, "total": total})

    def waiting(self, reason: str) -> None:
        self._send({"type": "state", "state": "waiting", "reason": reason})
        self.checkpoint()

    def running(self) -> None:
        self._send({"type": "state", "state": "running"})
        self.checkpoint()

    def _send(self, message: Mapping[str, Any]) -> None:
        with self._send_lock:
            self._connection.send(dict(message))


Worker = Callable[[dict[str, object], TaskContext], Any]


@dataclass(slots=True)
class _Runtime:
    process: mp.Process
    listener: Listener
    connection: Connection | None = None


class TaskRunner:
    def __init__(
        self,
        workspace: Path,
        worker: Worker,
        *,
        global_concurrency: int = 1,
        per_dataset_queue_limit: int = 5,
        cancel_grace: float = 5.0,
        terminal_history: int = 1_000,
        launch_timeout: float = 5.0,
    ) -> None:
        if global_concurrency <= 0:
            raise ValueError("global_concurrency must be positive")
        if per_dataset_queue_limit < 0:
            raise ValueError("per_dataset_queue_limit cannot be negative")
        self.workspace = Path(workspace)
        self.worker = worker
        self.global_concurrency = global_concurrency
        self.per_dataset_queue_limit = per_dataset_queue_limit
        self.cancel_grace = cancel_grace
        self.terminal_history = terminal_history
        self.launch_timeout = launch_timeout
        self.tasks_root = self.workspace / "tasks"
        self.handles_root = self.tasks_root / "handles"
        self.executions_root = self.tasks_root / "executions"
        self.logs_root = self.tasks_root / "logs"
        self._condition = threading.Condition(threading.RLock())
        self._handles: dict[str, HandleRecord] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._runtime: dict[str, _Runtime] = {}
        self._dataset_running: set[str] = set()
        self._launching: set[str] = set()
        self._monitor_threads: set[threading.Thread] = set()
        self._stop = threading.Event()
        self._started = False
        self._crashed = False
        self._dispatcher: threading.Thread | None = None
        self._mp_context = mp.get_context("spawn")

    def __enter__(self) -> TaskRunner:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback_value: Any) -> None:
        self.shutdown()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self.handles_root.mkdir(parents=True, exist_ok=True)
            self.executions_root.mkdir(parents=True, exist_ok=True)
            self.logs_root.mkdir(parents=True, exist_ok=True)
            self._load_and_recover()
            self._stop.clear()
            self._started = True
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="findata-task-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()

    def submit(
        self,
        dataset: str,
        operation: str,
        operands: Mapping[str, Any],
        *,
        owner: str = "user",
    ) -> str:
        self._ensure_started()
        normalized = _canonical_value(dict(operands))
        if not isinstance(normalized, dict):
            raise ValueError("operands must normalize to an object")
        key = _coalescing_key(dataset, operation, normalized)
        now = time.time()
        with self._condition:
            execution = None
            if key is not None:
                execution = next(
                    (
                        item
                        for item in self._executions.values()
                        if item.coalescing_key == key and item.status in {"queued", "running", "waiting"}
                    ),
                    None,
                )
            if execution is None:
                active = [
                    item
                    for item in self._executions.values()
                    if item.dataset == dataset and item.status in ACTIVE_STATES
                ]
                waiting_count = max(0, len(active) - 1)
                if waiting_count >= self.per_dataset_queue_limit:
                    raise QueueFullError(
                        f"dataset {dataset!r} already has {self.per_dataset_queue_limit} queued executions"
                    )
                execution_id = uuid.uuid4().hex
                execution = ExecutionRecord(
                    execution_id=execution_id,
                    dataset=dataset,
                    operation=operation,
                    operands=normalized,
                    status="queued",
                    created_at=now,
                    updated_at=now,
                    coalescing_key=key,
                )
                self._executions[execution_id] = execution
                self._persist_execution(execution)

            handle_id = uuid.uuid4().hex
            handle = HandleRecord(
                handle_id=handle_id,
                execution_id=execution.execution_id,
                dataset=dataset,
                operation=operation,
                owner=owner,
                status=execution.status,
                created_at=now,
                updated_at=now,
                progress=dict(execution.progress) if execution.progress else None,
            )
            execution.handle_ids.append(handle_id)
            execution.updated_at = now
            self._handles[handle_id] = handle
            self._persist_execution(execution)
            self._persist_handle(handle)
            self._condition.notify_all()
            return handle_id

    def status(self, handle_id: str) -> HandleRecord:
        with self._condition:
            handle = self._handles.get(handle_id)
            if handle is None:
                raise TaskNotFoundError(handle_id)
            return _copy_handle(handle)

    def list_handles(
        self,
        *,
        dataset: str | None = None,
        status: str | None = None,
    ) -> list[HandleRecord]:
        with self._condition:
            values = [
                _copy_handle(handle)
                for handle in self._handles.values()
                if (dataset is None or handle.dataset == dataset)
                and (status is None or handle.status == status)
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def wait(self, handle_id: str, *, timeout: float | None = None) -> HandleRecord:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                handle = self._handles.get(handle_id)
                if handle is None:
                    raise TaskNotFoundError(handle_id)
                if handle.status in TERMINAL_STATES:
                    return _copy_handle(handle)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"task {handle_id} did not reach a terminal state")
                self._condition.wait(remaining)

    def wait_for_status(
        self,
        handle_id: str,
        statuses: set[str],
        *,
        timeout: float | None = None,
    ) -> HandleRecord:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                handle = self._handles.get(handle_id)
                if handle is None:
                    raise TaskNotFoundError(handle_id)
                if handle.status in statuses:
                    return _copy_handle(handle)
                if handle.status in TERMINAL_STATES:
                    return _copy_handle(handle)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"task {handle_id} did not reach {statuses!r}")
                self._condition.wait(remaining)

    def cancel(self, handle_id: str) -> CancellationResult:
        with self._condition:
            handle = self._handles.get(handle_id)
            if handle is None:
                raise TaskNotFoundError(handle_id)
            if handle.status in TERMINAL_STATES:
                return CancellationResult(handle_id, False, handle.status)
            execution = self._executions[handle.execution_id]
            other_active = [
                self._handles[item]
                for item in execution.handle_ids
                if item != handle_id and self._handles[item].status not in TERMINAL_STATES
            ]
            now = time.time()
            if other_active:
                handle.status = "canceled"
                handle.updated_at = now
                handle.error = "canceled"
                self._persist_handle(handle)
                self._condition.notify_all()
                return CancellationResult(handle_id, True, "canceled")

            execution.cancel_requested = True
            execution.updated_at = now
            if execution.status == "queued" and execution.execution_id not in self._launching:
                execution.status = "canceled"
                handle.status = "canceled"
                handle.error = "canceled"
                handle.updated_at = now
                self._persist_execution(execution)
                self._persist_handle(handle)
                self._condition.notify_all()
                return CancellationResult(handle_id, False, "canceled")

            execution.status = "canceling"
            handle.status = "canceling"
            handle.updated_at = now
            runtime = self._runtime.get(execution.execution_id)
            if runtime is not None and runtime.connection is not None:
                try:
                    runtime.connection.send({"type": "cancel"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
            self._persist_execution(execution)
            self._persist_handle(handle)
            timer = threading.Thread(
                target=self._force_cancel_after_grace,
                args=(execution.execution_id,),
                daemon=True,
            )
            timer.start()
            self._condition.notify_all()
            return CancellationResult(handle_id, False, "canceling")

    def logs(self, handle_id: str) -> list[str]:
        with self._condition:
            handle = self._handles.get(handle_id)
            if handle is None:
                raise TaskNotFoundError(handle_id)
            path = self.logs_root / f"{handle.execution_id}.jsonl"
        if not path.exists():
            return []
        result: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and isinstance(item.get("message"), str):
                result.append(item["message"])
        return result

    def shutdown(self) -> None:
        if not self._started:
            return
        with self._condition:
            handles = [
                item.handle_id for item in self._handles.values() if item.status not in TERMINAL_STATES
            ]
        for handle_id in handles:
            try:
                self.cancel(handle_id)
            except TaskRunnerError:
                pass
        deadline = time.monotonic() + self.cancel_grace + 1.0
        with self._condition:
            while any(item.status not in TERMINAL_STATES for item in self._handles.values()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            runtimes = list(self._runtime.values())
        for runtime in runtimes:
            _terminate_process(runtime.process)
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=1)
        for thread in list(self._monitor_threads):
            thread.join(timeout=1)
        self._started = False

    def crash_for_test(self) -> None:
        """Simulate abrupt parent loss while leaving children for recovery tests."""
        self._crashed = True
        self._stop.set()
        with self._condition:
            for runtime in self._runtime.values():
                if runtime.connection is not None:
                    runtime.connection.close()
            self._condition.notify_all()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=1)
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self.start()

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            with self._condition:
                execution = self._next_execution()
                if execution is None:
                    self._condition.wait(0.1)
                    continue
                self._dataset_running.add(execution.dataset)
                self._launching.add(execution.execution_id)
                thread = threading.Thread(
                    target=self._run_execution,
                    args=(execution.execution_id,),
                    name=f"findata-execution-{execution.execution_id[:8]}",
                    daemon=True,
                )
                self._monitor_threads.add(thread)
                thread.start()

    def _next_execution(self) -> ExecutionRecord | None:
        running_count = len(self._launching) + sum(
            item.status in {"running", "waiting", "canceling"} for item in self._executions.values()
        )
        if running_count >= self.global_concurrency:
            return None
        candidates = sorted(
            (
                item
                for item in self._executions.values()
                if item.status == "queued"
                and item.execution_id not in self._launching
                and item.dataset not in self._dataset_running
            ),
            key=lambda item: item.created_at,
        )
        return candidates[0] if candidates else None

    def _run_execution(self, execution_id: str) -> None:
        listener: Listener | None = None
        process: mp.Process | None = None
        try:
            with self._condition:
                execution = self._executions[execution_id]
                request: dict[str, object] = {
                    "execution_id": execution_id,
                    "dataset": execution.dataset,
                    "operation": execution.operation,
                    "operands": execution.operands,
                }
            authkey = secrets.token_bytes(32)
            listener = Listener(("127.0.0.1", 0), authkey=authkey)
            socket_listener = getattr(getattr(listener, "_listener", None), "_socket", None)
            if socket_listener is not None:
                socket_listener.settimeout(self.launch_timeout)
            process = self._mp_context.Process(
                target=_child_main,
                args=(listener.address, authkey, self.worker, request),
                name=f"findata-task-{execution_id[:8]}",
            )
            process.start()
            runtime = _Runtime(process=process, listener=listener)
            with self._condition:
                if self._crashed:
                    return
                self._runtime[execution_id] = runtime
                execution = self._executions[execution_id]
                execution.pid = process.pid
                execution.process_start = _process_start(process.pid)
                execution.updated_at = time.time()
                self._persist_execution(execution)
            try:
                connection = listener.accept()
            except (socket.timeout, TimeoutError) as exc:
                raise TaskRunnerError("task process did not establish its authenticated channel") from exc
            runtime.connection = connection
            with self._condition:
                execution = self._executions[execution_id]
                self._launching.discard(execution_id)
                if execution.status == "queued":
                    execution.status = "running"
                    execution.updated_at = time.time()
                    self._update_active_handles(execution, status="running")
                    self._persist_execution(execution)
                if execution.cancel_requested:
                    connection.send({"type": "cancel"})
            terminal_message: dict[str, Any] | None = None
            while True:
                try:
                    message = connection.recv()
                except (EOFError, OSError):
                    break
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind in {"succeeded", "failed", "canceled"}:
                    terminal_message = message
                    break
                self._handle_message(execution_id, message)
            process.join(timeout=0.5)
            if terminal_message is None:
                if self._crashed:
                    return
                terminal_message = {
                    "type": "failed",
                    "error": f"worker exited without a terminal message (exit={process.exitcode})",
                }
            self._finish_execution(execution_id, terminal_message)
        except BaseException as exc:
            if not self._crashed:
                self._finish_execution(
                    execution_id,
                    {"type": "failed", "error": f"process launch/monitor failure: {exc}"},
                )
        finally:
            if process is not None and process.is_alive() and not self._crashed:
                _terminate_process(process)
            if listener is not None:
                listener.close()
            with self._condition:
                self._runtime.pop(execution_id, None)
                self._launching.discard(execution_id)
                execution = self._executions.get(execution_id)
                if execution is not None and execution.status in TERMINAL_STATES:
                    self._dataset_running.discard(execution.dataset)
                self._condition.notify_all()
                self._monitor_threads.discard(threading.current_thread())

    def _handle_message(self, execution_id: str, message: Mapping[str, Any]) -> None:
        with self._condition:
            execution = self._executions[execution_id]
            kind = message.get("type")
            if kind == "log":
                self._append_log(execution_id, str(message.get("message", "")))
                return
            if kind == "progress":
                execution.progress = {
                    "current": message.get("current", 0),
                    "total": message.get("total", 0),
                }
                self._update_active_handles(execution, progress=execution.progress)
            elif kind == "state" and message.get("state") in {"running", "waiting"}:
                execution.status = str(message["state"])
                self._update_active_handles(execution, status=execution.status)
            execution.updated_at = time.time()
            self._persist_execution(execution)
            self._condition.notify_all()

    def _finish_execution(self, execution_id: str, message: Mapping[str, Any]) -> None:
        with self._condition:
            execution = self._executions.get(execution_id)
            if execution is None or execution.status in TERMINAL_STATES:
                return
            kind = str(message.get("type"))
            if execution.cancel_requested:
                status = "canceled"
                result = None
                error = "canceled"
            elif kind == "succeeded":
                status = "succeeded"
                result = _jsonable(message.get("result"))
                error = None
            elif kind == "canceled":
                status = "canceled"
                result = None
                error = "canceled"
            else:
                status = "failed"
                result = None
                error = str(message.get("error") or "worker_failed")
            execution.status = status
            execution.result = result
            execution.error = error
            execution.updated_at = time.time()
            self._dataset_running.discard(execution.dataset)
            for handle_id in execution.handle_ids:
                handle = self._handles[handle_id]
                if handle.status in TERMINAL_STATES:
                    continue
                handle.status = status
                handle.result = result
                handle.error = error
                handle.progress = dict(execution.progress) if execution.progress else None
                handle.updated_at = execution.updated_at
                self._persist_handle(handle)
            self._persist_execution(execution)
            self._prune_history(execution.dataset)
            self._condition.notify_all()

    def _force_cancel_after_grace(self, execution_id: str) -> None:
        time.sleep(self.cancel_grace)
        with self._condition:
            execution = self._executions.get(execution_id)
            if execution is None or execution.status in TERMINAL_STATES:
                return
            runtime = self._runtime.get(execution_id)
        if runtime is not None:
            _terminate_process(runtime.process)
        self._finish_execution(execution_id, {"type": "canceled"})

    def _update_active_handles(
        self,
        execution: ExecutionRecord,
        *,
        status: str | None = None,
        progress: Mapping[str, int | float] | None = None,
    ) -> None:
        now = time.time()
        for handle_id in execution.handle_ids:
            handle = self._handles[handle_id]
            if handle.status in TERMINAL_STATES:
                continue
            if status is not None:
                handle.status = status
            if progress is not None:
                handle.progress = dict(progress)
            handle.updated_at = now
            self._persist_handle(handle)
        self._condition.notify_all()

    def _append_log(self, execution_id: str, message: str) -> None:
        path = self.logs_root / f"{execution_id}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"time": time.time(), "message": message}) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def _persist_handle(self, handle: HandleRecord) -> None:
        _atomic_json(self.handles_root / f"{handle.handle_id}.json", asdict(handle))

    def _persist_execution(self, execution: ExecutionRecord) -> None:
        _atomic_json(self.executions_root / f"{execution.execution_id}.json", asdict(execution))

    def _load_and_recover(self) -> None:
        self._handles.clear()
        self._executions.clear()
        for path in sorted(self.executions_root.glob("*.json")):
            data = _read_json(path)
            try:
                execution = ExecutionRecord(**data)
            except TypeError:
                continue
            self._executions[execution.execution_id] = execution
        for path in sorted(self.handles_root.glob("*.json")):
            data = _read_json(path)
            try:
                handle = HandleRecord(**data)
            except TypeError:
                continue
            self._handles[handle.handle_id] = handle
        now = time.time()
        for execution in self._executions.values():
            if execution.status not in ACTIVE_STATES:
                continue
            if execution.pid and execution.process_start:
                _terminate_if_same_process(execution.pid, execution.process_start)
            execution.status = "failed"
            execution.error = "server_interrupted"
            execution.updated_at = now
            self._persist_execution(execution)
            for handle_id in execution.handle_ids:
                handle = self._handles.get(handle_id)
                if handle is None or handle.status in TERMINAL_STATES:
                    continue
                handle.status = "failed"
                handle.error = "server_interrupted"
                handle.updated_at = now
                self._persist_handle(handle)

    def _prune_history(self, dataset: str) -> None:
        terminal = sorted(
            (
                handle
                for handle in self._handles.values()
                if handle.dataset == dataset and handle.status in TERMINAL_STATES
            ),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        for handle in terminal[self.terminal_history :]:
            self._handles.pop(handle.handle_id, None)
            (self.handles_root / f"{handle.handle_id}.json").unlink(missing_ok=True)
            execution = self._executions.get(handle.execution_id)
            if execution is None:
                continue
            execution.handle_ids = [item for item in execution.handle_ids if item != handle.handle_id]
            if execution.handle_ids:
                self._persist_execution(execution)
                continue
            self._executions.pop(execution.execution_id, None)
            (self.executions_root / f"{execution.execution_id}.json").unlink(missing_ok=True)
            (self.logs_root / f"{execution.execution_id}.jsonl").unlink(missing_ok=True)


def _child_main(
    address: tuple[str, int],
    authkey: bytes,
    worker: Worker,
    request: dict[str, object],
) -> None:
    connection = Client(address, authkey=authkey)
    canceled = threading.Event()

    def receive_control() -> None:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                return
            if isinstance(message, dict) and message.get("type") == "cancel":
                canceled.set()
                return

    receiver = threading.Thread(target=receive_control, daemon=True)
    receiver.start()
    context = TaskContext(connection, canceled)
    try:
        result = worker(request, context)
        context.checkpoint()
        connection.send({"type": "succeeded", "result": _jsonable(result)})
    except TaskCancelled:
        connection.send({"type": "canceled"})
    except BaseException as exc:
        connection.send(
            {
                "type": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=20),
            }
        )
    finally:
        connection.close()


def _coalescing_key(dataset: str, operation: str, operands: Mapping[str, Any]) -> str | None:
    if operation not in COALESCING_OPERATIONS:
        return None
    return json.dumps(
        {"dataset": dataset, "operation": operation, "operands": operands},
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        normalized = [_canonical_value(item) for item in value]
        if all(isinstance(item, (str, int, float, bool, type(None))) for item in normalized):
            return sorted(dict.fromkeys(normalized), key=lambda item: (type(item).__name__, repr(item)))
        return normalized
    return value


def _copy_handle(handle: HandleRecord) -> HandleRecord:
    return HandleRecord(**asdict(handle))


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain an object")
    return value


def _process_start(pid: int | None) -> str | None:
    if not pid:
        return None
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value or None


def _terminate_if_same_process(pid: int, expected_start: str) -> None:
    if _process_start(pid) != expected_start:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _terminate_process(process: mp.Process) -> None:
    if not process.is_alive():
        process.join(timeout=0.1)
        return
    process.terminate()
    process.join(timeout=0.2)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.2)
