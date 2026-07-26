from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from findata.events import EventStore
from findata.storage import Workspace


@dataclass(frozen=True, slots=True)
class CronJob:
    dataset: str
    expression: str
    timezone: str
    enabled: bool
    source: str
    last_run: str | None
    next_run: str | None


class CronSchedule:
    def __init__(self, expression: str, timezone: str) -> None:
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron expression must have five fields")
        try:
            self.zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone {timezone!r}") from exc
        self.expression = expression
        self.timezone = timezone
        self.minutes = _field(fields[0], 0, 59)
        self.hours = _field(fields[1], 0, 23)
        self.days = _field(fields[2], 1, 31)
        self.months = _field(fields[3], 1, 12)
        self.weekdays = _field(fields[4], 0, 7, sunday=True)

    def next_after(self, instant: datetime) -> datetime:
        if instant.tzinfo is None:
            raise ValueError("cron instants must be timezone-aware")
        candidate = instant.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = candidate + timedelta(days=370)
        while candidate <= limit:
            local = candidate.astimezone(self.zone)
            if local.fold == 0 and self.matches(local):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("cron expression has no occurrence within one year")

    def matches(self, local: datetime) -> bool:
        cron_weekday = (local.weekday() + 1) % 7
        return (
            local.minute in self.minutes
            and local.hour in self.hours
            and local.day in self.days
            and local.month in self.months
            and cron_weekday in self.weekdays
        )

    def skipped_between(self, start: datetime, end: datetime) -> list[str]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("cron gap interval must be ordered and timezone-aware")
        first_date = start.astimezone(self.zone).date()
        last_date = end.astimezone(self.zone).date()
        result: list[str] = []
        day = first_date
        while day <= last_date:
            for hour in self.hours:
                for minute in self.minutes:
                    local = datetime.combine(day, time(hour, minute))
                    if not self.matches(local):
                        continue
                    candidates = [local.replace(tzinfo=self.zone, fold=fold) for fold in (0, 1)]
                    valid = any(
                        candidate.astimezone(UTC).astimezone(self.zone).replace(tzinfo=None)
                        == local
                        for candidate in candidates
                    )
                    approximate = candidates[0].astimezone(UTC)
                    if not valid and start < approximate <= end:
                        result.append(local.isoformat())
            day += timedelta(days=1)
        return sorted(set(result))


class CronManager:
    def __init__(
        self,
        workspace: Workspace,
        events: EventStore,
        *,
        submit: Callable[[str, str, dict[str, object]], Any],
        provider_ready: Callable[[str], bool],
        update_ready: Callable[[str], bool],
        suggested: Mapping[str, tuple[str, str]],
    ) -> None:
        self.workspace = workspace
        self.events = events
        self.submit = submit
        self.provider_ready = provider_ready
        self.update_ready = update_ready
        self.suggested = dict(suggested)

    def list_jobs(self, *, now: datetime | None = None) -> list[CronJob]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        state = self._state()
        return [self._job(dataset, state.get(dataset, {}), current) for dataset in self.suggested]

    def enable(self, dataset: str, *, now: datetime | None = None) -> CronJob:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        self._validate_dataset(dataset)
        if not self.provider_ready(dataset):
            self.events.record(
                "cron_skipped",
                "error",
                f"cannot enable {dataset}: provider is not ready",
                dataset=dataset,
            )
            raise ValueError(f"provider for {dataset} is not ready")
        if not self.update_ready(dataset):
            self.events.record(
                "cron_skipped",
                "error",
                f"cannot enable {dataset}: update is not ready",
                dataset=dataset,
            )
            raise ValueError(f"update for {dataset} is not ready")
        state = self._state()
        entry = dict(state.get(dataset) or {})
        entry["enabled"] = True
        entry["last_checked"] = current.isoformat()
        job = self._job(dataset, entry, current)
        entry["next_run"] = job.next_run
        state[dataset] = entry
        self._save(state)
        return self._job(dataset, entry, current)

    def disable(self, dataset: str) -> CronJob:
        self._validate_dataset(dataset)
        state = self._state()
        entry = dict(state.get(dataset) or {})
        entry["enabled"] = False
        entry["next_run"] = None
        state[dataset] = entry
        self._save(state)
        return self._job(dataset, entry, datetime.now(UTC))

    def set_schedule(self, dataset: str, expression: str, timezone: str) -> CronJob:
        self._validate_dataset(dataset)
        CronSchedule(expression, timezone)
        state = self._state()
        entry = dict(state.get(dataset) or {})
        entry.update({"expression": expression, "timezone": timezone, "source": "override"})
        if entry.get("enabled"):
            entry["next_run"] = (
                CronSchedule(expression, timezone).next_after(datetime.now(UTC)).isoformat()
            )
        state[dataset] = entry
        self._save(state)
        return self._job(dataset, entry, datetime.now(UTC))

    def reset(self, dataset: str) -> CronJob:
        self._validate_dataset(dataset)
        state = self._state()
        entry = dict(state.get(dataset) or {})
        for key in ("expression", "timezone", "source"):
            entry.pop(key, None)
        if entry.get("enabled"):
            expression, timezone = self.suggested[dataset]
            entry["next_run"] = (
                CronSchedule(expression, timezone).next_after(datetime.now(UTC)).isoformat()
            )
        state[dataset] = entry
        self._save(state)
        return self._job(dataset, entry, datetime.now(UTC))

    def tick(self, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
        state = self._state()
        changed = False
        for dataset in self.suggested:
            entry = dict(state.get(dataset) or {})
            if not entry.get("enabled"):
                continue
            job = self._job(dataset, entry, current)
            last_checked_text = entry.get("last_checked")
            if last_checked_text:
                schedule = CronSchedule(job.expression, job.timezone)
                last_checked = datetime.fromisoformat(last_checked_text)
                skipped = (
                    schedule.skipped_between(last_checked, current)
                    if last_checked < current
                    else []
                )
                for wall_time in skipped:
                    self.events.record(
                        "cron_dst_gap",
                        "warning",
                        f"scheduled wall time did not exist for {dataset}",
                        dataset=dataset,
                        wall_time=wall_time,
                        timezone=job.timezone,
                    )
            entry["last_checked"] = current.isoformat()
            state[dataset] = entry
            changed = True
            due = datetime.fromisoformat(job.next_run) if job.next_run else None
            if due is None or due > current:
                continue
            if not self.provider_ready(dataset) or not self.update_ready(dataset):
                self.events.record(
                    "cron_skipped",
                    "error",
                    f"scheduled update skipped for {dataset}",
                    dataset=dataset,
                )
            else:
                try:
                    self.submit(dataset, "update", {})
                except Exception as exc:
                    self.events.record(
                        "cron_skipped",
                        "error",
                        f"scheduled update rejected for {dataset}: {exc}",
                        dataset=dataset,
                    )
                else:
                    entry["last_run"] = current.isoformat()
            schedule = CronSchedule(job.expression, job.timezone)
            entry["next_run"] = schedule.next_after(current).isoformat()
            state[dataset] = entry
            changed = True
        if changed:
            self._save(state)

    def note_shutdown(self, now: datetime | None = None) -> None:
        self.workspace.set_config(
            "cron.last_seen", (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        )

    def recover(self, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        state = self._state()
        changed = False
        for dataset in self.suggested:
            entry = dict(state.get(dataset) or {})
            if not entry.get("enabled") or not entry.get("next_run"):
                continue
            due = datetime.fromisoformat(entry["next_run"])
            if due <= current:
                self.events.record(
                    "cron_missed",
                    "warning",
                    f"scheduled update was missed for {dataset}",
                    dataset=dataset,
                    scheduled_for=due.isoformat(),
                )
                job = self._job(dataset, entry, current)
                entry["next_run"] = (
                    CronSchedule(job.expression, job.timezone).next_after(current).isoformat()
                )
                state[dataset] = entry
                changed = True
        if changed:
            self._save(state)

    def _job(self, dataset: str, entry: dict[str, Any], now: datetime) -> CronJob:
        suggested_expression, suggested_timezone = self.suggested[dataset]
        expression = str(entry.get("expression") or suggested_expression)
        timezone = str(entry.get("timezone") or suggested_timezone)
        enabled = bool(entry.get("enabled", False))
        next_run = entry.get("next_run")
        if enabled and not next_run:
            next_run = CronSchedule(expression, timezone).next_after(now).isoformat()
        return CronJob(
            dataset=dataset,
            expression=expression,
            timezone=timezone,
            enabled=enabled,
            source=str(entry.get("source") or "suggested"),
            last_run=entry.get("last_run"),
            next_run=str(next_run) if next_run else None,
        )

    def _state(self) -> dict[str, Any]:
        value = self.workspace.get_config("cron.jobs", {})
        return dict(value) if isinstance(value, dict) else {}

    def _save(self, state: dict[str, Any]) -> None:
        self.workspace.set_config("cron.jobs", state)

    def _validate_dataset(self, dataset: str) -> None:
        if dataset not in self.suggested:
            raise ValueError(f"unknown scheduled dataset {dataset!r}")


def _field(text: str, minimum: int, maximum: int, *, sunday: bool = False) -> set[int]:
    result: set[int] = set()
    for item in text.split(","):
        step = 1
        if "/" in item:
            item, step_text = item.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be positive")
        if item == "*":
            start, end = minimum, maximum
        elif "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(item)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"cron field {text!r} is out of range")
        result.update(range(start, end + 1, step))
    if sunday and 7 in result:
        result.discard(7)
        result.add(0)
    return result
