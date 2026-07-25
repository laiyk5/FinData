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
          <div className="page-head">
            <h1>
              <StatusBadge status={task.status} />{" "}
              <Link to={`/datasets/${encodeURIComponent(task.dataset)}`} className="mono">
                {task.dataset}
              </Link>{" "}
              <span>{task.operation}</span>{" "}
              <span className="muted">by {ownerLabel(task.owner)}</span>
            </h1>
            <FreshnessNote lastUpdated={taskLive.lastUpdated} />
          </div>

          <div className="panel">
            <ProgressBar progress={task.progress} />
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>elapsed</dt>
              <dd>{formatDuration(task.updated_at - task.created_at)}</dd>
              <dt>created</dt>
              <dd>
                <Time unix={task.created_at} />
              </dd>
              <dt>updated</dt>
              <dd>
                <Time unix={task.updated_at} />
              </dd>
              {task.stage && (
                <>
                  <dt>stage</dt>
                  <dd>{task.stage}</dd>
                </>
              )}
              {task.reason && (
                <>
                  <dt>reason</dt>
                  <dd>{task.reason}</dd>
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
              <dt>handle</dt>
              <dd>
                <CopyableId id={task.handle_id} />
              </dd>
              <dt>execution</dt>
              <dd>
                <CopyableId id={task.execution_id} />
              </dd>
            </dl>

            <div className="form-row" style={{ marginTop: 10 }}>
              {!terminal && (
                <CancelTaskButton task={task} onChanged={() => void taskLive.refresh()} />
              )}
              {(task.status === "failed" || task.status === "canceled") && (
                <RetryTaskButton task={task} />
              )}
            </div>
          </div>

          {(explain || explainError) && (
            <div className="panel">
              <h3>Explanation</h3>
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
                      <h3>Inspect with</h3>
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
            </div>
          )}

          <div className="panel">
            <div className="form-row" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0 }}>Logs</h3>
              <span className="form-row" style={{ alignItems: "center" }}>
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
                  follow{follow && terminal ? " (task terminal — stopped)" : ""}
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
          </div>

          <details className="panel details-panel">
            <summary>Details</summary>
            <dl className="kv" style={{ marginTop: 8 }}>
              <dt>handle id</dt>
              <dd className="mono">{task.handle_id}</dd>
              <dt>execution id</dt>
              <dd className="mono">{task.execution_id}</dd>
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
