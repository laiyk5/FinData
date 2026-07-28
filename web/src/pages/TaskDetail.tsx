import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import {
  TERMINAL_STATUSES,
  errorMessage,
  explainTask,
  getTask,
  getTaskLogs,
  type TaskExplanation,
  type TaskLogEntry,
} from "../api";
import { CancelTaskButton, RetryTaskButton } from "../components/TaskActions";
import { ownerLabel } from "../components/TaskList";
import {
  ConnectionWarning,
  CopyableCode,
  CopyableId,
  EmptyState,
  ErrorBanner,
  FreshnessNote,
  JsonBlock,
  Loading,
  ProgressBar,
  StatusBadge,
  Time,
} from "../components/common";
import { formatDuration } from "../format";
import { useLiveData, usePoll } from "../hooks";
import { dedupLogLines, type DedupedLogLine } from "../logEntries";

type SeverityFilter = "all" | "info" | "warning" | "error";

function lineMatches(line: DedupedLogLine, filter: SeverityFilter): boolean {
  if (filter === "all") return true;
  if (filter === "info") return line.view.severity === null || line.view.severity === "info";
  return line.view.severity === filter;
}

function datasetLabel(name: string): string {
  return name.slice(name.lastIndexOf("/") + 1).replace(/_/g, " ");
}

function operationLabel(operation: string): string {
  return operation.replace(/[_-]/g, " ");
}

function taskSummary(status: string): string {
  if (status === "succeeded") return "Completed successfully";
  if (status === "failed") return "Needs attention";
  if (status === "canceled") return "Canceled";
  if (status === "queued") return "Waiting to start";
  if (status === "waiting") return "Waiting for a dependency";
  return "Work in progress";
}

export default function TaskDetailPage() {
  const { id = "" } = useParams();
  const [logs, setLogs] = useState<TaskLogEntry[] | null>(null);
  const [logsError, setLogsError] = useState<unknown>(null);
  const [follow, setFollow] = useState(false);
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [explain, setExplain] = useState<TaskExplanation | null>(null);
  const [explainError, setExplainError] = useState<unknown>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  const taskLive = useLiveData(useCallback(() => getTask(id), [id]), 1_000);
  const task = taskLive.data;
  const terminal = task !== null && TERMINAL_STATUSES.has(task.status);

  const loadLogs = useCallback(async () => {
    try {
      const r = await getTaskLogs(id);
      setLogs(r.items);
      setLogsError(null);
    } catch (err) {
      setLogsError(err);
    }
  }, [id]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  // Follow mode streams logs at the live cadence; one final load lands when
  // the task turns terminal.
  usePoll(loadLogs, 1_000, follow && !terminal);
  const wasTerminal = useRef(false);
  useEffect(() => {
    if (terminal && !wasTerminal.current) {
      wasTerminal.current = true;
      void loadLogs();
    }
  }, [terminal, loadLogs]);

  useEffect(() => {
    if (follow && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, follow]);

  // Explain loads automatically for failed/canceled tasks.
  useEffect(() => {
    if (task && (task.status === "failed" || task.status === "canceled")) {
      explainTask(id)
        .then((e) => {
          setExplain(e);
          setExplainError(null);
        })
        .catch((err) => setExplainError(err));
    }
  }, [id, task?.status]); // re-run when the task reaches a terminal state

  if (!task && !taskLive.error) return <Loading />;

  const lines = dedupLogLines(logs ?? []).filter((l) => lineMatches(l, severity));

  return (
    <div>
      <ConnectionWarning error={taskLive.error} />
      {task && (
        <>
          <header className="task-detail-header">
            <div>
              <Link to="/tasks" className="task-detail-back">← Tasks</Link>
              <div className="task-detail-title-row">
                <div>
                  <h1>{operationLabel(task.operation)} {datasetLabel(task.dataset)}</h1>
                  <p>Dataset: <Link to={`/datasets/${encodeURIComponent(task.dataset)}`}>{datasetLabel(task.dataset)}</Link> <span className="mono">{task.dataset}</span></p>
                </div>
                <StatusBadge status={task.status} />
              </div>
            </div>
            <FreshnessNote lastUpdated={taskLive.lastUpdated} />
          </header>

          <section className={`task-detail-summary status-${task.status}`}>
            <div>
              <p className="eyebrow">Task status</p>
              <h2>{taskSummary(task.status)}</h2>
              <p>{task.reason ?? task.stage ?? `Started by ${ownerLabel(task.owner)}.`}</p>
            </div>
            <div className="task-detail-summary-actions">
              {!terminal && <CancelTaskButton task={task} onChanged={() => void taskLive.refresh()} size="btn" />}
              {(task.status === "failed" || task.status === "canceled") && <RetryTaskButton task={task} size="btn" />}
            </div>
          </section>

          <section className="panel task-detail-progress-panel">
            <div className="task-detail-section-heading"><div><p className="eyebrow">{terminal ? "Run summary" : "Progress"}</p><h2>{terminal ? task.status === "succeeded" ? "Completed run" : "Run details" : "Current progress"}</h2></div><span className="muted">Updated <Time unix={task.updated_at} /></span></div>
            {!terminal && <>
              <ProgressBar progress={task.progress} />
              {(task.stage || task.reason) && <p className="task-detail-stage">{task.stage ?? task.reason}</p>}
            </>}
            <dl className="kv task-detail-facts">
              <dt>started by</dt>
              <dd>{ownerLabel(task.owner)}</dd>
              <dt>started</dt>
              <dd>
                <Time unix={task.created_at} />
              </dd>
              <dt>duration</dt>
              <dd>{formatDuration(task.updated_at - task.created_at)}</dd>
              {!terminal && task.stage && (
                <>
                  <dt>current stage</dt>
                  <dd>{task.stage}</dd>
                </>
              )}
              {task.diagnostic_counts &&
                (task.diagnostic_counts.warning > 0 || task.diagnostic_counts.error > 0) && (
                  <>
                    <dt>diagnostics</dt>
                    <dd>
                      {task.diagnostic_counts.warning > 0 && (
                        <span className="badge severity-warning">
                          {task.diagnostic_counts.warning} warning
                        </span>
                      )}{" "}
                      {task.diagnostic_counts.error > 0 && (
                        <span className="badge severity-error">
                          {task.diagnostic_counts.error} error
                        </span>
                      )}
                    </dd>
                  </>
                )}
            </dl>
          </section>

          {(explain || explainError) && (
            <section className="panel task-explanation-panel">
              <div className="task-detail-section-heading"><div><p className="eyebrow">Recovery</p><h2>What happened</h2></div></div>
              <ErrorBanner error={explainError} />
              {explain && (
                <>
                  <dl className="kv">
                    <dt>reason</dt>
                    <dd>{explain.reason ?? "—"}</dd>
                  </dl>
                  <h3>Diagnostics</h3>
                  {explain.diagnostics.length === 0 ? (
                    <EmptyState>No diagnostics recorded.</EmptyState>
                  ) : (
                    <div className="log-pane">
                      {dedupLogLines(explain.diagnostics).map((line, i) => (
                        <LogLine key={i} line={line} />
                      ))}
                    </div>
                  )}
                  {Object.keys(explain.inspection).length > 0 && (
                    <>
                      <h3>Helpful commands</h3>
                      <ul className="inspection-list">
                        {Object.entries(explain.inspection).map(([label, cmd]) => (
                          <li key={label}>
                            <span className="muted">{label}:</span>{" "}
                            <CopyableCode text={cmd} />
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </section>
          )}

          <section className="panel task-log-panel">
            <div className="task-detail-section-heading">
              <div><p className="eyebrow">Activity</p><h2>Task log</h2></div>
              <span className="task-log-controls">
                <span className="filter-chips">
                  {(["all", "info", "warning", "error"] as SeverityFilter[]).map((f) => (
                    <button
                      key={f}
                      className={`chip filter-chip ${severity === f ? "active" : ""}`}
                      onClick={() => setSeverity(f)}
                    >
                      {f}
                    </button>
                  ))}
                </span>
                <label className="muted" style={{ display: "inline-flex", gap: 5 }}>
                  <input
                    type="checkbox"
                    checked={follow}
                    onChange={(e) => setFollow(e.target.checked)}
                  />
                  Follow live{follow && terminal ? " (task finished)" : ""}
                </label>
              </span>
            </div>
            <ConnectionWarning error={logsError} />
            {logs === null && !logsError && <Loading label="loading logs…" />}
            {logs !== null && lines.length === 0 && (
              <EmptyState>
                {logs.length === 0
                  ? "No log entries."
                  : "No log entries match this severity filter."}
              </EmptyState>
            )}
            {lines.length > 0 && (
              <div className="log-pane" ref={logRef}>
                {lines.map((line, i) => (
                  <LogLine key={i} line={line} />
                ))}
              </div>
            )}
          </section>

          <details className="panel details-panel task-technical-details">
            <summary>Technical details <span>IDs, result, and raw error</span></summary>
            <dl className="kv" style={{ marginTop: 8 }}>
              <dt>handle id</dt>
              <dd><CopyableId id={task.handle_id} /></dd>
              <dt>execution id</dt>
              <dd><CopyableId id={task.execution_id} /></dd>
              {task.subscriber_count > 1 && (
                <>
                  <dt>subscribers</dt>
                  <dd className="muted">
                    coalesced with {task.subscriber_count - 1} other requester
                    {task.subscriber_count - 1 === 1 ? "" : "s"}
                  </dd>
                </>
              )}
              {task.error && (
                <>
                  <dt>error</dt>
                  <dd className="warning-text">{task.error}</dd>
                </>
              )}
              {task.result !== undefined && task.result !== null && (
                <>
                  <dt>result</dt>
                  <dd>
                    <JsonBlock value={task.result} label="result JSON" />
                  </dd>
                </>
              )}
            </dl>
          </details>
        </>
      )}
    </div>
  );
}

function LogLine({ line }: { line: DedupedLogLine }) {
  const { view, count } = line;
  const className = view.severity ? `log-line diag ${view.severity}` : "log-line";
  return (
    <div className={className}>
      {view.timestamp !== null && <span className="mono muted">{view.timestamp} </span>}
      {view.text}
      {count > 1 && <span className="dedup-badge">×{count}</span>}
    </div>
  );
}
