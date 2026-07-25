import { Link } from "react-router";
import { TERMINAL_STATUSES, type TaskHandle } from "../api";
import { CancelTaskButton, RetryTaskButton } from "./TaskActions";
import { CopyableId, ProgressBar, StatusBadge, Time } from "./common";

/** Owners that submitted via a task trigger read as "triggered". */
export function ownerLabel(owner: string): string {
  return owner.startsWith("trigger:") ? "triggered" : owner;
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
 * Shared task table used by the Tasks page and the dataset Activity tab.
 * Rows offer inline actions: Cancel for active tasks, Retry and Explain for
 * failed/canceled ones.
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
    <table>
      <thead>
        <tr>
          <th>id</th>
          {showDataset && <th>dataset</th>}
          <th>operation</th>
          <th>owner</th>
          <th>status</th>
          <th>progress</th>
          <th>updated</th>
          <th>actions</th>
        </tr>
      </thead>
      <tbody>
        {items.map((t) => {
          const active = !TERMINAL_STATUSES.has(t.status);
          const retriable = t.status === "failed" || t.status === "canceled";
          return (
            <tr key={t.handle_id}>
              <td>
                <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`}>
                  <CopyableId id={t.handle_id} />
                </Link>
              </td>
              {showDataset && (
                <td>
                  <Link to={`/datasets/${encodeURIComponent(t.dataset)}`} className="mono">
                    {t.dataset}
                  </Link>
                </td>
              )}
              <td>{t.operation}</td>
              <td className="muted">{ownerLabel(t.owner)}</td>
              <td>
                <StatusBadge status={t.status} /> <DiagnosticBadges task={t} />
              </td>
              <td>
                {t.progress ? (
                  <ProgressBar progress={t.progress} />
                ) : (
                  <span className="muted">{t.stage ?? t.reason ?? "—"}</span>
                )}
                {t.progress && (t.stage || t.reason) && (
                  <div className="muted progress-sub">{t.stage ?? t.reason}</div>
                )}
              </td>
              <td>
                <Time unix={t.updated_at} />
              </td>
              <td className="row-actions">
                {active && <CancelTaskButton task={t} onChanged={onChanged} />}
                {retriable && (
                  <>
                    <RetryTaskButton task={t} />{" "}
                    <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`}>Explain</Link>
                  </>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
