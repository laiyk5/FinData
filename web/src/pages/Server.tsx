import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  getSystemStatus,
  listTasks,
  TERMINAL_STATUSES,
  type SystemStatus,
  type TaskHandle,
  type TaskStatus,
} from "../api";
import {
  ConnectionWarning,
  CopyableCode,
  EmptyState,
  FreshnessNote,
  Loading,
  Time,
} from "../components/common";
import { formatBytes, formatDuration, formatRelativeTime } from "../format";
import { useLiveData } from "../hooks";
import { taskTimelinePoints, timelineTicks } from "../taskTimeline";

interface ServerData {
  status: SystemStatus;
  tasks: TaskHandle[];
}

/** Ticking clock for the live uptime display. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Timeline mark color: active work vs terminal outcome. */
function timelineClass(status: TaskStatus): string {
  if (!TERMINAL_STATUSES.has(status)) return "tl-active";
  if (status === "succeeded") return "tl-ok";
  if (status === "failed") return "tl-err";
  return "tl-neutral";
}

/** Read-only server status page: identity, capacity, and task load. */
export default function ServerPage() {
  const loader = useCallback(async (): Promise<ServerData> => {
    const [status, tasks] = await Promise.all([getSystemStatus(), listTasks({ all: true })]);
    return { status, tasks: tasks.items };
  }, []);

  // Slow cadence: this page is diagnostics, not a live work view.
  const live = useLiveData(loader, 10_000);
  const now = useNow(1_000);
  const navigate = useNavigate();
  const { data } = live;

  if (!data && !live.error) return <Loading />;

  const status = data?.status ?? null;
  const uptimeSeconds = status ? now / 1000 - status.started_at : null;
  const points = taskTimelinePoints(data?.tasks ?? [], now / 1000);
  const workspaceDisk = status?.workspace_disk ?? null;

  return (
    <div>
      <div className="page-head">
        <h1>Server</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={live.error} />

      {status && workspaceDisk && (
        <>
          <div className="panel">
            <h3>Identity</h3>
            <dl className="kv">
              <dt>workspace</dt>
              <dd>
                <CopyableCode text={status.workspace} />
              </dd>
              <dt>pid</dt>
              <dd className="mono">{status.pid}</dd>
              <dt>server version</dt>
              <dd className="mono">{status.version}</dd>
              <dt>webui version</dt>
              <dd className="mono">v{__APP_VERSION__}</dd>
              <dt>listen address</dt>
              <dd className="mono">{window.location.host}</dd>
              <dt>status</dt>
              <dd>{status.status}</dd>
              {uptimeSeconds !== null && (
                <>
                  <dt>uptime</dt>
                  <dd>
                    <span title={`started ${new Date(status.started_at * 1000).toISOString()}`}>
                      {formatDuration(uptimeSeconds)}
                    </span>{" "}
                    <span className="muted">
                      (started <Time unix={status.started_at} />)
                    </span>
                  </dd>
                </>
              )}
            </dl>
          </div>

          <div className="panel">
            <h3>Capacity</h3>
            <div className="workspace-usage">
              <div className="workspace-usage-total">
                <span className="card-value">{formatBytes(workspaceDisk.total_bytes)}</span>{" "}
                <span className="muted">workspace usage</span>
              </div>
              {workspaceDisk.breakdown.length > 0 && (
                <div className="workspace-breakdown">
                  {workspaceDisk.breakdown.map((entry) => {
                    const pct =
                      workspaceDisk.total_bytes > 0
                        ? (entry.bytes / workspaceDisk.total_bytes) * 100
                        : 0;
                    return (
                      <div key={entry.name} className="workspace-breakdown-row">
                        <span className="mono workspace-breakdown-name">{entry.name}</span>
                        <span className="workspace-breakdown-bar">
                          <span
                            className="workspace-breakdown-fill"
                            style={{ width: `${Math.max(pct, entry.bytes > 0 ? 1.5 : 0)}%` }}
                          />
                        </span>
                        <span className="mono workspace-breakdown-size">
                          {formatBytes(entry.bytes)}
                        </span>
                        <span className="muted workspace-breakdown-pct">
                          {pct.toFixed(1)}%
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            <dl className="kv" style={{ marginTop: 12 }}>
              <dt>running tasks</dt>
              <dd>
                <Link to="/tasks?status=active">{status.running_tasks}</Link>
              </dd>
              <dt>total handles</dt>
              <dd className="mono">{status.tasks}</dd>
            </dl>
            <h3>Queue lengths</h3>
            {Object.keys(status.queue_lengths).length === 0 ? (
              <p className="muted">No per-dataset queues.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>dataset</th>
                    <th>queued</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(status.queue_lengths).map(([dataset, length]) => (
                    <tr key={dataset}>
                      <td>
                        <Link to={`/datasets/${encodeURIComponent(dataset)}`} className="mono">
                          {dataset}
                        </Link>
                      </td>
                      <td>{length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="panel">
            <h3>Task activity — last 24 hours</h3>
            {points.length === 0 ? (
              <EmptyState>No tasks created in the last 24 hours.</EmptyState>
            ) : (
              <>
                <div className="timeline-track" role="img" aria-label="task activity timeline">
                  {points.map((point) => (
                    <button
                      key={point.task.handle_id}
                      type="button"
                      className={`timeline-dot ${timelineClass(point.task.status)}`}
                      style={{ left: `${point.x * 100}%` }}
                      title={`${point.task.dataset} ${point.task.operation} — ${point.task.status}, ${formatRelativeTime(point.task.created_at, now / 1000)}`}
                      onClick={() => navigate(`/tasks/${point.task.handle_id}`)}
                    />
                  ))}
                </div>
                <div className="timeline-ticks">
                  {timelineTicks().map((tick) => (
                    <span key={tick.label} className="muted">
                      {tick.label}
                    </span>
                  ))}
                </div>
                <div className="timeline-legend">
                  <span className="timeline-legend-dot tl-ok" /> succeeded
                  <span className="timeline-legend-dot tl-err" /> failed
                  <span className="timeline-legend-dot tl-neutral" /> canceled
                  <span className="timeline-legend-dot tl-active" /> active
                </div>
              </>
            )}
            <p className="muted histogram-note">
              each mark is a task — click to open it
            </p>
          </div>
        </>
      )}
    </div>
  );
}
