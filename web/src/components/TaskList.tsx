import { Link } from "react-router";
import { TERMINAL_STATUSES, type TaskHandle } from "../api";
import { CancelTaskButton, RetryTaskButton } from "./TaskActions";
import { CopyableId, ProgressBar, StatusBadge, Time } from "./common";

/** Owners that submitted via a task trigger read as "triggered". */
export function ownerLabel(owner: string): string {
  return owner.startsWith("trigger:") ? "triggered" : owner;
}

function datasetLabel(name: string): string {
  return name.slice(name.lastIndexOf("/") + 1).replace(/_/g, " ");
}

function operationLabel(operation: string): string {
  return operation.replace(/[_-]/g, " ");
}

function DiagnosticBadges({ task }: { task: TaskHandle }) {
  const counts = task.diagnostic_counts;
  if (!counts || (counts.warning === 0 && counts.error === 0)) return null;
  return (
    <span className="diag-badges">
      {counts.warning > 0 && (
        <span className="badge severity-warning">{counts.warning} warning</span>
      )}
      {counts.error > 0 && (
        <span className="badge severity-error">{counts.error} error</span>
      )}
    </span>
  );
}

/**
 * Shared task cards used by the Tasks page and the dataset Activity tab.
 */
export function TaskList({
  items,
  onChanged,
  showDataset = true,
}: {
  items: TaskHandle[];
  onChanged?: () => void;
  showDataset?: boolean;
}) {
  return (
    <div className="task-list">
      {items.map((t) => {
          const active = !TERMINAL_STATUSES.has(t.status);
          const retriable = t.status === "failed" || t.status === "canceled";
          return (
            <article key={t.handle_id} className={`task-card status-${t.status}`}>
              <div className="task-card-heading">
                <div>
                  <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`} className="task-card-title">
                    {operationLabel(t.operation)} {showDataset ? datasetLabel(t.dataset) : "task"}
                  </Link>
                  {showDataset && (
                    <div className="task-card-context">
                      Dataset: <Link to={`/datasets/${encodeURIComponent(t.dataset)}`}>{datasetLabel(t.dataset)}</Link>
                      <span className="mono">{t.dataset}</span>
                    </div>
                  )}
                </div>
                <span><StatusBadge status={t.status} /> <DiagnosticBadges task={t} /></span>
              </div>
              {active && (
                <div className="task-card-progress">
                  {t.progress ? (
                    <ProgressBar progress={t.progress} />
                  ) : (
                    <span className="muted">{t.stage ?? t.reason ?? "Preparing task…"}</span>
                  )}
                  {t.progress && (t.stage || t.reason) && (
                    <div className="muted progress-sub">{t.stage ?? t.reason}</div>
                  )}
                </div>
              )}
              {!active && t.reason && <p className="task-card-outcome">{t.reason}</p>}
              <footer className="task-card-footer">
                <span className="muted">Started by {ownerLabel(t.owner)} · updated <Time unix={t.updated_at} /></span>
                <span className="task-card-actions">
                {active && <CancelTaskButton task={t} onChanged={onChanged} />}
                {retriable && (
                  <>
                    <RetryTaskButton task={t} />{" "}
                  </>
                )}
                <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`}>{retriable ? "Review failure" : "View details"}</Link>
                <CopyableId id={t.handle_id} />
                </span>
              </footer>
            </article>
          );
      })}
    </div>
  );
}
