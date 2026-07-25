import { useCallback, useState } from "react";
import { Link } from "react-router";
import {
  TERMINAL_STATUSES,
  getSystemStatus,
  listDatasets,
  listDatasetsStatus,
  listEvents,
  listProviders,
  listTasks,
  type DatasetDescription,
  type DatasetStatus,
  type EventRecord,
  type Provider,
  type SystemStatus,
  type TaskHandle,
} from "../api";
import { DatasetCard } from "../components/DatasetCard";
import { RetryTaskButton } from "../components/TaskActions";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  Loading,
  ProgressBar,
  SeverityBadge,
  StatusBadge,
  Time,
} from "../components/common";
import { useLiveData } from "../hooks";

interface HomeData {
  status: SystemStatus;
  providers: Provider[];
  unreadEvents: EventRecord[];
  tasks: TaskHandle[];
  datasets: DatasetStatus[];
  descriptions: Record<string, DatasetDescription>;
}

function isActive(task: TaskHandle): boolean {
  return !TERMINAL_STATUSES.has(task.status);
}

async function loadHome(): Promise<HomeData> {
  const [status, providers, events, tasks, datasets, descriptions] = await Promise.all([
    getSystemStatus(),
    listProviders(),
    listEvents({ unread: true }),
    listTasks(),
    listDatasetsStatus(),
    listDatasets(),
  ]);
  return {
    status,
    providers: providers.items,
    unreadEvents: events.items.filter((e) => !e.acknowledged),
    tasks: tasks.items,
    datasets: datasets.items,
    descriptions: Object.fromEntries(descriptions.items.map((d) => [d.name, d])),
  };
}

export default function HomePage() {
  // Adaptive cadence: medium while any task is active, slow when idle.
  const [hasActive, setHasActive] = useState(false);
  const loader = useCallback(async () => {
    const data = await loadHome();
    setHasActive(data.tasks.some(isActive));
    return data;
  }, []);
  const live = useLiveData(loader, hasActive ? 2_500 : 12_000);
  const { data } = live;

  if (!data && !live.error) return <Loading />;

  const failed = data?.tasks.filter((t) => t.status === "failed") ?? [];
  const problemEvents =
    data?.unreadEvents.filter(
      (e) => (e.severity === "warning" || e.severity === "error") && e.kind !== "cron_missed",
    ) ?? [];
  const cronMissed = data?.unreadEvents.filter((e) => e.kind === "cron_missed") ?? [];
  const badProviders =
    data?.providers.filter((p) => !p.ready || p.configured === false) ?? [];
  const staleDatasets =
    data?.datasets.filter((d) => d.state === "ready" && !d.update_ready) ?? [];
  const needsAttention =
    failed.length + problemEvents.length + cronMissed.length +
    badProviders.length + staleDatasets.length;

  const liveTasks = data?.tasks.filter(isActive) ?? [];

  return (
    <div>
      <div className="page-head">
        <h1>Home</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <ConnectionWarning error={data ? live.error : null} />
      {!data && <ConnectionWarning error={live.error} />}

      {data && (
        <>
          <h2>Needs attention</h2>
          {needsAttention === 0 ? (
            <EmptyState>Nothing needs attention — no failed tasks, unread warnings, unready providers, missed cron jobs, or stalled datasets.</EmptyState>
          ) : (
            <div className="attention-list">
              {failed.map((t) => (
                <div key={`task-${t.handle_id}`} className="attention-row">
                  <span className="badge severity-error">failed task</span>
                  <span>
                    <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`} className="mono">
                      {t.dataset} {t.operation}
                    </Link>{" "}
                    <span className="muted">{t.reason ?? t.error ?? ""}</span>
                  </span>
                  <span className="row-actions">
                    <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`}>Explain</Link>
                    <RetryTaskButton task={t} />
                  </span>
                </div>
              ))}
              {problemEvents.map((e) => (
                <div key={`event-${e.event_id}`} className="attention-row">
                  <SeverityBadge severity={e.severity} />
                  <span>
                    {e.message}{" "}
                    <span className="muted">
                      <Time unix={e.timestamp} />
                    </span>
                  </span>
                  <span className="row-actions">
                    <Link to="/events?unread">Open events</Link>
                  </span>
                </div>
              ))}
              {cronMissed.map((e) => (
                <div key={`cron-${e.event_id}`} className="attention-row">
                  <span className="badge severity-warning">missed cron</span>
                  <span>{e.message}</span>
                  <span className="row-actions">
                    <Link to="/cron">Open cron</Link>
                  </span>
                </div>
              ))}
              {badProviders.map((p) => (
                <div key={`provider-${p.name}`} className="attention-row">
                  <span className="badge severity-warning">provider</span>
                  <span>
                    <span className="mono">{p.name}</span>{" "}
                    <span className="muted">needs configuration</span>
                  </span>
                  <span className="row-actions">
                    <Link to="/providers">Fix provider</Link>
                  </span>
                </div>
              ))}
              {staleDatasets.map((d) => (
                <div key={`dataset-${d.name}`} className="attention-row">
                  <span className="badge severity-warning">dataset</span>
                  <span>
                    <span className="mono">{d.name}</span>{" "}
                    <span className="muted">has data but update is blocked</span>
                  </span>
                  <span className="row-actions">
                    <Link to={`/datasets/${encodeURIComponent(d.name)}?tab=settings`}>
                      Open settings
                    </Link>
                  </span>
                </div>
              ))}
            </div>
          )}

          <h2>Live now</h2>
          {liveTasks.length === 0 ? (
            <EmptyState>No live work right now.</EmptyState>
          ) : (
            <div className="live-list">
              {liveTasks.map((t) => (
                <div key={t.handle_id} className="live-row">
                  <StatusBadge status={t.status} />
                  <Link to={`/tasks/${encodeURIComponent(t.handle_id)}`} className="mono">
                    {t.dataset} {t.operation}
                  </Link>
                  {t.stage && <span className="muted">{t.stage}</span>}
                  <ProgressBar progress={t.progress} />
                </div>
              ))}
            </div>
          )}

          <h2>Dataset health</h2>
          {data.datasets.length === 0 ? (
            <EmptyState>No datasets registered.</EmptyState>
          ) : (
            <div className="health-grid">
              {data.datasets.map((d) => {
                const desc = data.descriptions[d.name];
                return (
                  <DatasetCard
                    key={d.name}
                    name={d.name}
                    state={d.state}
                    provider={d.provider}
                    providerReady={d.provider_ready}
                    updateReady={d.update_ready}
                    missingRequired={
                      desc?.settings
                        .filter((s) => s.required && !s.configured)
                        .map((s) => s.key) ?? []
                    }
                    capabilities={desc?.capabilities ?? {}}
                    publicationId={d.publication_id}
                    status={d}
                    tasks={data.tasks.filter((t) => t.dataset === d.name)}
                  />
                );
              })}
            </div>
          )}

          <footer className="server-footer muted">
            <Link to="/server">server</Link> {data.status.status} · pid {data.status.pid} ·{" "}
            {data.status.tasks} handles ·{" "}
            <span className="mono workspace-path" title={data.status.workspace}>
              {data.status.workspace}
            </span>
            {Object.keys(data.status.queue_lengths).length > 0 && (
              <>
                {" "}
                · queues:{" "}
                {Object.entries(data.status.queue_lengths)
                  .map(([name, n]) => `${name} ${n}`)
                  .join(", ")}
              </>
            )}{" "}
            · <Link to="/server">details →</Link>
          </footer>
        </>
      )}
    </div>
  );
}
