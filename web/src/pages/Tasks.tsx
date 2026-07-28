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

function statusCount(items: TaskHandle[], filter: StatusFilter): number {
  return items.filter((task) => matches(task, filter)).length;
}

function datasetLabel(name: string): string {
  return name.slice(name.lastIndexOf("/") + 1).replace(/_/g, " ");
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

  const taskItems = live.data ?? [];
  const visible = taskItems.filter((t) => matches(t, filter));

  return (
    <div>
      <header className="tasks-page-header">
        <div>
          <h1>Tasks</h1>
          <p>Follow data operations, review outcomes, and retry work that needs attention.</p>
        </div>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </header>
      {live.data && (
        <div className="task-status-summary">
          {(["active", "failed", "succeeded", "canceled"] as const).map((status) => (
            <button
              key={status}
              className={`task-status-stat ${filter === status ? "active" : ""}`}
              onClick={() => setFilter(status)}
            >
              <strong>{statusCount(taskItems, status)}</strong>
              <span>{status === "active" ? "in progress" : status}</span>
            </button>
          ))}
        </div>
      )}
      <div className="task-filter-bar">
        <div className="task-filter-group">
          <span className="task-filter-label">Show</span>
          {STATUS_FILTERS.map((f) => (
            <button
              key={f}
              className={`chip filter-chip ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f === "active" ? "in progress" : f}
            </button>
          ))}
        </div>
        <label className="task-dataset-filter">
          <span className="task-filter-label">Dataset</span>
          <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
            <option value="">All datasets</option>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {datasetLabel(d)} — {d}
              </option>
            ))}
          </select>
        </label>
        <label className="task-history-toggle">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
          Include full retained history
        </label>
      </div>
      <ConnectionWarning error={live.error} />
      {live.data && visible.length === 0 && (
        <EmptyState>
          No {filter === "all" ? "" : `${filter} `}tasks
          {dataset ? ` for ${datasetLabel(dataset)}` : ""}. Submitted tasks appear here.
        </EmptyState>
      )}
      {visible.length > 0 && (
        <TaskList items={visible} onChanged={() => void live.refresh()} />
      )}
    </div>
  );
}
