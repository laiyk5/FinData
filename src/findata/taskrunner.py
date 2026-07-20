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
    reason: str | None = None
    stage: str | None = None


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
    triggered_handle_ids: list[str] = field(default_factory=list)
    trigger_depth: int = 0
    reason: str | None = None
    stage: str | None = None


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
        self._dependency_condition = threading.Condition()
        self._dependency_results: dict[str, Mapping[str, Any]] = {}

    def checkpoint(self) -> None:
        if self._canceled.is_set():
            raise TaskCancelled()

    def log(self, message: str) -> None:
        self._send({"type": "log", "message": str(message)})

    def progress(self, current: int | float, total: int | float) -> None:
        if total < 0 or current < 0:
            raise ValueError("progress values cannot be negative")
        self._send({"type": "progress", "current": current, "total": total})

    def stage(self, value: str) -> None:
        self._send({"type": "stage", "stage": str(value)})

    def waiting(self, reason: str) -> None:
        self._send({"type": "state", "state": "waiting", "reason": reason})
        self.checkpoint()

    def running(self) -> None:
        self._send({"type": "state", "state": "running"})
        self.checkpoint()

    def begin_subtask(self, *, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("subtask timeout must be positive")
        self._send({"type": "subtask", "timeout": float(timeout)})

    def end_subtask(self) -> None:
        self._send({"type": "subtask_complete"})

    def fulfill(self, dataset: str, requirement: Mapping[str, Any]) -> Any:
        request_id = uuid.uuid4().hex
        self._send(
            {
                "type": "dependency",
                "request_id": request_id,
                "dataset": dataset,
                "requirement": dict(requirement),
            }
        )
        with self._dependency_condition:
            while request_id not in self._dependency_results:
                self.checkpoint()
                self._dependency_condition.wait(0.1)
            response = self._dependency_results.pop(request_id)
        if not response.get("ok"):
            raise RuntimeError(f"dependency {dataset} failed: {response.get('error')}")
        return response.get("result")

    def receive_control(self, message: Mapping[str, Any]) -> None:
        if message.get("type") == "cancel":
            self._canceled.set()
        elif message.get("type") == "dependency_result":
            request_id = str(message.get("request_id"))
            with self._dependency_condition:
                self._dependency_results[request_id] = dict(message)
                self._dependency_condition.notify_all()

    def _send(self, message: Mapping[str, Any]) -> None:
        with self._send_lock:
            self._connection.send(dict(message))


Worker = Callable[[dict[str, object], TaskContext], Any]
DependencyResolver = Callable[[str, str, dict[str, object]], tuple[str, dict[str, object]]]


@dataclass(slots=True)
class _Runtime:
    process: mp.Process
    listener: Listener
    connection: Connection | None = None
    liveness_deadline: float | None = None
    liveness_warned: bool = False


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
        event_sink: Callable[..., Any] | None = None,
        dependency_resolver: DependencyResolver | None = None,
        max_trigger_depth: int = 8,
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
        self.event_sink = event_sink
        self.dependency_resolver = dependency_resolver
        self.max_trigger_depth = max_trigger_depth
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
        self._liveness_monitor: threading.Thread | None = None
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
            self._liveness_monitor = threading.Thread(
                target=self._liveness_loop,
                name="findata-liveness-monitor",
                daemon=True,
            )
            self._liveness_monitor.start()

    def submit(
        self,
        dataset: str,
        operation: str,
        operands: Mapping[str, Any],
        *,
        owner: str = "user",
        _trigger_depth: int = 0,
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
                    self._event(
                        "queue_rejected",
                        "error",
                        f"task rejected because the {dataset} queue is full",
                        dataset=dataset,
                        operation=operation,
                    )
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
                    trigger_depth=_trigger_depth,
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
                reason=execution.reason,
                stage=execution.stage,
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

    def subscriber_count(self, handle_id: str) -> int:
        with self._condition:
            handle = self._handles.get(handle_id)
            if handle is None:
                raise TaskNotFoundError(handle_id)
            execution = self._executions[handle.execution_id]
            return sum(
                self._handles[item].status not in TERMINAL_STATES
                for item in execution.handle_ids
                if item in self._handles
            )

    def runtime_status(self) -> dict[str, Any]:
        with self._condition:
            queue_lengths: dict[str, int] = {}
            for execution in self._executions.values():
                if execution.status == "queued":
                    queue_lengths[execution.dataset] = queue_lengths.get(execution.dataset, 0) + 1
            running = sum(
                execution.status in {"running", "waiting", "canceling"}
                for execution in self._executions.values()
            )
            return {"running_tasks": running, "queue_lengths": queue_lengths}

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
            for triggered_handle_id in list(execution.triggered_handle_ids):
                triggered = self._handles.get(triggered_handle_id)
                if triggered is not None and triggered.status not in TERMINAL_STATES:
                    self.cancel(triggered_handle_id)
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
        if self._liveness_monitor is not None:
            self._liveness_monitor.join(timeout=1)
            self._liveness_monitor = None
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

    def _liveness_loop(self) -> None:
        while not self._stop.wait(0.02):
            expired: list[tuple[str, str, str]] = []
            now = time.monotonic()
            with self._condition:
                for execution_id, runtime in self._runtime.items():
                    if (
                        runtime.liveness_deadline is not None
                        and now >= runtime.liveness_deadline
                        and not runtime.liveness_warned
                    ):
                        runtime.liveness_warned = True
                        execution = self._executions.get(execution_id)
                        if execution is not None:
                            expired.append(
                                (execution_id, execution.dataset, execution.operation)
                            )
            for execution_id, dataset, operation in expired:
                self._event(
                    "liveness_timeout",
                    "warning",
                    f"task {execution_id} exceeded its negotiated subtask timeout",
                    execution_id=execution_id,
                    dataset=dataset,
                    operation=operation,
                )

    def _next_execution(self) -> ExecutionRecord | None:
        running_count = len(self._launching) + sum(
            item.status in {"running", "canceling"} for item in self._executions.values()
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
                if kind == "dependency":
                    self._handle_dependency(execution_id, message, connection)
                    continue
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
            elif kind == "stage":
                execution.stage = str(message.get("stage") or "") or None
                self._update_active_handles(execution, stage=execution.stage, update_stage=True)
            elif kind == "subtask":
                timeout = float(message.get("timeout", 0))
                if timeout <= 0:
                    return
                runtime = self._runtime.get(execution_id)
                if runtime is not None:
                    runtime.liveness_deadline = time.monotonic() + timeout
                    runtime.liveness_warned = False
            elif kind == "subtask_complete":
                runtime = self._runtime.get(execution_id)
                if runtime is not None:
                    runtime.liveness_deadline = None
                    runtime.liveness_warned = False
            elif kind == "state" and message.get("state") in {"running", "waiting"}:
                execution.status = str(message["state"])
                execution.reason = (
                    str(message.get("reason"))
                    if execution.status == "waiting" and message.get("reason")
                    else None
                )
                self._update_active_handles(
                    execution, status=execution.status, reason=execution.reason, update_reason=True
                )
            execution.updated_at = time.time()
            self._persist_execution(execution)
            self._condition.notify_all()

    def _handle_dependency(
        self,
        execution_id: str,
        message: Mapping[str, Any],
        connection: Connection,
    ) -> None:
        request_id = str(message.get("request_id"))
        target = str(message.get("dataset"))
        requirement = message.get("requirement")
        try:
            if self.dependency_resolver is None:
                raise ValueError("task worker requested an undeclared dependency")
            if not isinstance(requirement, dict):
                raise ValueError("dependency requirement must be an object")
            with self._condition:
                parent = self._executions[execution_id]
                depth = parent.trigger_depth + 1
                if depth > self.max_trigger_depth:
                    raise ValueError(f"dependency depth exceeds maximum {self.max_trigger_depth}")
                operation, operands = self.dependency_resolver(
                    parent.dataset, target, dict(requirement)
                )
                if depth > 3:
                    self._event(
                        "dependency_depth",
                        "warning",
                        f"dependency depth {depth} reached by {target}",
                        dataset=target,
                        parent_dataset=parent.dataset,
                    )
                owner_handle = next(
                    (
                        handle_id
                        for handle_id in parent.handle_ids
                        if self._handles[handle_id].status not in TERMINAL_STATES
                    ),
                    parent.handle_ids[0],
                )
            child_handle = self.submit(
                target,
                operation,
                operands,
                owner=f"trigger:{owner_handle}",
                _trigger_depth=depth,
            )
            with self._condition:
                parent = self._executions[execution_id]
                parent.triggered_handle_ids.append(child_handle)
                parent.status = "waiting"
                parent.reason = f"dependency:{target}"
                self._update_active_handles(
                    parent, status="waiting", reason=parent.reason, update_reason=True
                )
                self._persist_execution(parent)
            child = self.wait(child_handle)
            if child.status != "succeeded":
                raise RuntimeError(child.error or child.status)
            response: dict[str, Any] = {
                "type": "dependency_result",
                "request_id": request_id,
                "ok": True,
                "result": child.result,
            }
        except Exception as exc:
            response = {
                "type": "dependency_result",
                "request_id": request_id,
                "ok": False,
                "error": str(exc),
            }
        finally:
            with self._condition:
                parent = self._executions.get(execution_id)
                if parent is not None and parent.status == "waiting":
                    parent.status = "running"
                    parent.reason = None
                    self._update_active_handles(
                        parent, status="running", reason=None, update_reason=True
                    )
                    self._persist_execution(parent)
        try:
            connection.send(response)
        except (BrokenPipeError, EOFError, OSError):
            pass

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
            execution.reason = None
            execution.stage = None
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
                handle.reason = None
                handle.stage = None
                handle.updated_at = execution.updated_at
                self._persist_handle(handle)
            self._persist_execution(execution)
            if status == "failed":
                self._event(
                    "task_failed",
                    "error",
                    f"task {execution.execution_id} failed: {error}",
                    dataset=execution.dataset,
                    operation=execution.operation,
                    execution_id=execution.execution_id,
                )
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
        reason: str | None = None,
        update_reason: bool = False,
        stage: str | None = None,
        update_stage: bool = False,
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
            if update_reason:
                handle.reason = reason
            if update_stage:
                handle.stage = stage
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
            self._event(
                "task_failed",
                "error",
                f"task {execution.execution_id} failed: server_interrupted",
                dataset=execution.dataset,
                operation=execution.operation,
                execution_id=execution.execution_id,
            )

    def _event(self, kind: str, severity: str, message: str, **context: Any) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(kind, severity, message, **context)
        except Exception:
            # Event reporting cannot corrupt the task state transition it describes.
            pass

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
    context = TaskContext(connection, canceled)

    def receive_control() -> None:
        while True:
            try:
                message = connection.recv()
            except (EOFError, OSError):
                return
            if isinstance(message, dict):
                context.receive_control(message)
                if message.get("type") == "cancel":
                    return

    receiver = threading.Thread(target=receive_control, daemon=True)
    receiver.start()
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
