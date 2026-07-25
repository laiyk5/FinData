import { Link } from "react-router";
import { TERMINAL_STATUSES, type DatasetStatus, type TaskHandle } from "../api";
import { formatBytes } from "../format";
import { CopyableId, DatasetStateBadge, StatusBadge, Time } from "./common";
import { ClockIcon } from "./icons";

/** Time-accumulating datasets declare `capabilities.time_accumulating`. */
export function isTimeAccumulating(capabilities: Record<string, unknown>): boolean {
  return Boolean(capabilities["time_accumulating"]);
}

/** The most recent terminal task, by `updated_at`. */
export function newestTerminalTask(tasks: TaskHandle[]): TaskHandle | null {
  let best: TaskHandle | null = null;
  for (const task of tasks) {
    if (!TERMINAL_STATUSES.has(task.status)) continue;
    if (best === null || task.updated_at > best.updated_at) best = task;
  }
  return best;
}

/**
 * Dataset state presented as freshness, not an abstract badge: `uninitialized`
 * shows "no data"; a dataset with committed data shows last-maintenance
 * freshness from its newest terminal task ("updated 3h ago").
 */
export function DatasetFreshness({
  state,
  tasks,
}: {
  state: string;
  tasks: TaskHandle[];
}) {
  if (state !== "ready") return <DatasetStateBadge state={state} />;
  const last = newestTerminalTask(tasks);
  if (last === null) {
    return (
      <span className="freshness-line muted">
        <ClockIcon /> has data
      </span>
    );
  }
  return (
    <span className="freshness-line muted">
      <ClockIcon /> updated <Time unix={last.updated_at} />
    </span>
  );
}

/**
 * Coverage presentation follows the dataset's declared structure:
 * time-accumulating datasets show `N keys, [start → end)` (or "no coverage
 * yet"); complete-replacement datasets track no coverage and instead show the
 * current publication and the last maintenance activity.
 */
export function DatasetCoverage({
  capabilities,
  publicationId,
  status,
  tasks = [],
}: {
  capabilities: Record<string, unknown>;
  publicationId: string | null;
  status: DatasetStatus | null;
  /** Tasks for this dataset; the newest terminal one is the last maintenance. */
  tasks?: TaskHandle[];
}) {
  if (isTimeAccumulating(capabilities)) {
    return (
      <span className="coverage-line">
        {status && status.covered_keys !== null ? (
          <span className="muted">
            {status.covered_keys} keys, [{status.coverage_start ?? "?"} →{" "}
            {status.coverage_end ?? "?"})
          </span>
        ) : (
          <span className="muted">no coverage yet</span>
        )}
        <StorageFact status={status} />
      </span>
    );
  }

  const last = newestTerminalTask(tasks);
  return (
    <span className="coverage-line">
      <span className="muted">complete replacement — no coverage tracked</span>
      <span className="coverage-facts">
        <span className="muted">publication:</span>{" "}
        {publicationId ? (
          <CopyableId id={publicationId} />
        ) : (
          <span className="muted">—</span>
        )}{" "}
        <span className="muted">· last update:</span>{" "}
        {last ? (
          <Link to={`/tasks/${encodeURIComponent(last.handle_id)}`}>
            <StatusBadge status={last.status} /> <Time unix={last.updated_at} />
          </Link>
        ) : (
          <span className="muted">none yet</span>
        )}
      </span>
      <StorageFact status={status} />
    </span>
  );
}

/** Quiet on-disk storage fact; omitted when the server reports no file. */
function StorageFact({ status }: { status: DatasetStatus | null }) {
  if (status?.storage_bytes === null || status?.storage_bytes === undefined) return null;
  return (
    <span className="coverage-facts muted">storage: {formatBytes(status.storage_bytes)}</span>
  );
}
