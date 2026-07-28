import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  getPlugins,
  getSystemStatus,
  listTasks,
  reloadPlugins,
  removePlugin,
  restorePlugin,
  TERMINAL_STATUSES,
  type PluginRegistry,
  type SystemStatus,
  type TaskHandle,
  type TaskStatus,
} from "../api";
import {
  ConnectionWarning,
  CopyableCode,
  EmptyState,
  ErrorBanner,
  FreshnessNote,
  Loading,
  Time,
} from "../components/common";
import { formatBytes, formatDuration, formatRelativeTime } from "../format";
import { useLiveData } from "../hooks";
import { taskTimelinePoints, timelineTicks } from "../taskTimeline";

interface ServerData { status: SystemStatus; tasks: TaskHandle[]; plugins: PluginRegistry; }

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function timelineClass(status: TaskStatus): string {
  if (!TERMINAL_STATUSES.has(status)) return "tl-active";
  if (status === "succeeded") return "tl-ok";
  if (status === "failed") return "tl-err";
  return "tl-neutral";
}

function readableName(name: string): string {
  return name.slice(name.lastIndexOf("/") + 1).replace(/[-_]/g, " ");
}

/** Operational server overview with advanced maintenance controls kept secondary. */
export default function ServerPage() {
  const loader = useCallback(async (): Promise<ServerData> => {
    const [status, tasks, plugins] = await Promise.all([getSystemStatus(), listTasks({ all: true }), getPlugins()]);
    return { status, tasks: tasks.items, plugins };
  }, []);

  const live = useLiveData(loader, 10_000);
  const now = useNow(1_000);
  const navigate = useNavigate();
  const { data } = live;
  const [pluginBusy, setPluginBusy] = useState(false);
  const [pluginError, setPluginError] = useState<unknown>(null);

  const changePlugins = async (action: () => Promise<PluginRegistry>): Promise<void> => {
    setPluginBusy(true);
    setPluginError(null);
    try {
      await action();
      await live.refresh();
    } catch (error) {
      setPluginError(error);
    } finally {
      setPluginBusy(false);
    }
  };

  if (!data && !live.error) return <Loading />;

  const status = data?.status ?? null;
  const workspaceDisk = status?.workspace_disk ?? null;
  const plugins = data?.plugins ?? { providers: [], datasets: [], blocked: [] };
  const uptimeSeconds = status ? now / 1000 - status.started_at : 0;
  const points = taskTimelinePoints(data?.tasks ?? [], now / 1000);
  const queued = status ? Object.values(status.queue_lengths).reduce((total, value) => total + value, 0) : 0;
  const pluginCount = plugins.providers.length + plugins.datasets.length;

  return (
    <div>
      <header className="server-page-header">
        <div><h1>Server</h1><p>Workspace health, active work, and maintenance controls.</p></div>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </header>
      <ConnectionWarning error={live.error} />
      <ErrorBanner error={pluginError} />

      {status && workspaceDisk && (
        <>
          <section className="server-health-summary">
            <div className="server-health-mark">✓</div>
            <div>
              <p className="eyebrow">Server status</p>
              <h2>{status.running_tasks > 0 ? "Work in progress" : "Server is running normally"}</h2>
              <p>{status.running_tasks > 0 ? `${status.running_tasks} task${status.running_tasks === 1 ? " is" : "s are"} running now.` : "No tasks are currently running."}</p>
            </div>
            <Link className="btn" to={status.running_tasks > 0 ? "/tasks?status=active" : "/tasks"}>
              {status.running_tasks > 0 ? "View active tasks" : "View tasks"}
            </Link>
          </section>

          <div className="server-metric-grid">
            <section className="card server-metric-card"><span>Workspace storage</span><strong>{formatBytes(workspaceDisk.total_bytes)}</strong><small>Committed data and workspace files</small></section>
            <section className="card server-metric-card"><span>Task queue</span><strong>{status.running_tasks + queued}</strong><small>{status.running_tasks} running · {queued} waiting</small></section>
            <section className="card server-metric-card"><span>Available plugins</span><strong>{pluginCount}</strong><small>{plugins.providers.length} providers · {plugins.datasets.length} datasets</small></section>
          </div>

          <section className="panel server-activity-panel">
            <div className="server-section-heading"><div><p className="eyebrow">Recent work</p><h2>Task activity</h2></div><Link to="/tasks">All tasks →</Link></div>
            {points.length === 0 ? <EmptyState>No tasks created in the last 24 hours.</EmptyState> : <>
              <div className="timeline-track" role="img" aria-label="task activity timeline">
                {points.map((point) => <button key={point.task.handle_id} type="button" className={`timeline-dot ${timelineClass(point.task.status)}`} style={{ left: `${point.x * 100}%` }} title={`${point.task.dataset} ${point.task.operation} — ${point.task.status}, ${formatRelativeTime(point.task.created_at, now / 1000)}`} onClick={() => navigate(`/tasks/${point.task.handle_id}`)} />)}
              </div>
              <div className="timeline-ticks">{timelineTicks().map((tick) => <span key={tick.label} className="muted">{tick.label}</span>)}</div>
              <div className="timeline-legend"><span className="timeline-legend-dot tl-ok" /> succeeded <span className="timeline-legend-dot tl-err" /> failed <span className="timeline-legend-dot tl-neutral" /> canceled <span className="timeline-legend-dot tl-active" /> active</div>
              <p className="muted histogram-note">Each mark is a task — select one to open it.</p>
            </>}
          </section>

          <details className="panel details-panel server-details-panel">
            <summary>Storage details <span>{formatBytes(workspaceDisk.total_bytes)}</span></summary>
            {workspaceDisk.breakdown.length === 0 ? <p className="muted">No storage breakdown is available.</p> : <div className="workspace-breakdown">
              {workspaceDisk.breakdown.map((entry) => {
                const percentage = workspaceDisk.total_bytes > 0 ? (entry.bytes / workspaceDisk.total_bytes) * 100 : 0;
                return <div key={entry.name} className="workspace-breakdown-row"><span className="workspace-breakdown-name">{readableName(entry.name)}</span><span className="workspace-breakdown-bar"><span className="workspace-breakdown-fill" style={{ width: `${Math.max(percentage, entry.bytes > 0 ? 1.5 : 0)}%` }} /></span><span className="mono workspace-breakdown-size">{formatBytes(entry.bytes)}</span><span className="muted workspace-breakdown-pct">{percentage.toFixed(1)}%</span></div>;
              })}
            </div>}
          </details>

          <details className="panel details-panel server-details-panel">
            <summary>Queue details <span>{queued === 0 ? "No tasks waiting" : `${queued} waiting`}</span></summary>
            {Object.keys(status.queue_lengths).length === 0 ? <p className="muted">No dataset queues are waiting.</p> : <div className="server-queue-list">{Object.entries(status.queue_lengths).map(([dataset, length]) => <Link key={dataset} to={`/datasets/${encodeURIComponent(dataset)}`}><span>{readableName(dataset)}</span><span className="muted">{length} waiting</span></Link>)}</div>}
          </details>

          <details className="panel details-panel server-details-panel">
            <summary>Plugin maintenance <span>{plugins.blocked.length > 0 ? `${plugins.blocked.length} removed` : "All available"}</span></summary>
            <div className="server-maintenance-head"><p className="muted">Reload newly installed plugins, or remove a plugin from this workspace. Plugin changes wait until active tasks finish.</p><button type="button" className="btn btn-secondary" disabled={pluginBusy || status.running_tasks > 0} title={status.running_tasks > 0 ? "Wait for active tasks to finish" : undefined} onClick={() => void changePlugins(reloadPlugins)}>Reload plugins</button></div>
            <div className="plugin-runtime-list">{[...plugins.providers, ...plugins.datasets].map((plugin) => <div key={plugin.name} className="plugin-runtime-row"><div><strong>{readableName(plugin.name)}</strong><div className="mono muted">{plugin.name}</div></div><button type="button" className="btn btn-xs" disabled={pluginBusy || status.running_tasks > 0} onClick={() => { if (window.confirm(`Remove ${plugin.name} from this workspace?`)) void changePlugins(() => removePlugin(plugin.name)); }}>Remove</button></div>)}</div>
            {plugins.blocked.length > 0 && <div className="plugin-runtime-blocked"><strong>Removed plugins</strong>{plugins.blocked.map((name) => <button key={name} type="button" className="btn btn-xs" disabled={pluginBusy || status.running_tasks > 0} onClick={() => void changePlugins(() => restorePlugin(name))}>Restore {readableName(name)}</button>)}</div>}
          </details>

          <details className="panel details-panel server-details-panel">
            <summary>Technical information <span>Workspace and server identifiers</span></summary>
            <dl className="kv server-identity-list"><dt>workspace</dt><dd><CopyableCode text={status.workspace} /></dd><dt>server version</dt><dd className="mono">{status.version}</dd><dt>webui version</dt><dd className="mono">v{__APP_VERSION__}</dd><dt>listen address</dt><dd className="mono">{window.location.host}</dd><dt>process ID</dt><dd className="mono">{status.pid}</dd><dt>uptime</dt><dd><span title={`started ${new Date(status.started_at * 1000).toISOString()}`}>{formatDuration(uptimeSeconds)}</span> <span className="muted">(started <Time unix={status.started_at} />)</span></dd><dt>task handles</dt><dd>{status.tasks}</dd></dl>
          </details>
        </>
      )}
    </div>
  );
}
