import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { TERMINAL_STATUSES, listDatasets, listTasks, type TaskHandle } from "../api";
import { TaskList } from "../components/TaskList";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
} from "../components/common";
import { useLiveData } from "../hooks";

const STATUS_FILTERS = ["active", "succeeded", "failed", "canceled", "all"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

function matches(task: TaskHandle, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return !TERMINAL_STATUSES.has(task.status);
  return task.status === filter;
}

export default function TasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const statusParam = searchParams.get("status");
  const filter: StatusFilter = (STATUS_FILTERS as readonly string[]).includes(statusParam ?? "")
    ? (statusParam as StatusFilter)
    : "all";
  const dataset = searchParams.get("dataset") ?? "";
  const [all, setAll] = useState(false);
  const [datasets, setDatasets] = useState<string[]>([]);
  const [hasActive, setHasActive] = useState(false);

  useEffect(() => {
    listDatasets()
      .then((r) => setDatasets(r.items.map((d) => d.name)))
      .catch(() => undefined);
  }, []);

  const loader = useCallback(async () => {
    const r = await listTasks({ dataset: dataset || undefined, all });
    setHasActive(r.items.some((t) => !TERMINAL_STATUSES.has(t.status)));
    return r.items;
  }, [dataset, all]);

  // Fast while any row is active, slow otherwise.
  const live = useLiveData<TaskHandle[]>(loader, hasActive ? 2_500 : 12_000);

  const setFilter = (f: StatusFilter): void => {
    const next: Record<string, string> = {};
    if (f !== "all") next.status = f;
    if (dataset) next.dataset = dataset;
    setSearchParams(next);
  };

  const setDataset = (d: string): void => {
    const next: Record<string, string> = {};
    if (filter !== "all") next.status = filter;
    if (d) next.dataset = d;
    setSearchParams(next);
  };

  if (!live.data && !live.error) return <Loading />;

  const visible = (live.data ?? []).filter((t) => matches(t, filter));

  return (
    <div>
      <div className="page-head">
        <h1>Tasks</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <div className="filters">
        <span className="filter-chips">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f}
              className={`chip filter-chip ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </span>
        <label>
          dataset
          <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
            <option value="">all</option>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
          show all retained
        </label>
      </div>
      <ConnectionWarning error={live.error} />
      {live.data && visible.length === 0 && (
        <EmptyState>
          No {filter === "all" ? "" : `${filter} `}tasks
          {dataset ? ` for ${dataset}` : ""} — submitted tasks appear here.
        </EmptyState>
      )}
      {visible.length > 0 && (
        <TaskList items={visible} onChanged={() => void live.refresh()} />
      )}
    </div>
  );
}
