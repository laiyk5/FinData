import { useCallback, useState } from "react";
import { Link } from "react-router";
import {
  TERMINAL_STATUSES,
  ackEvent,
  getSystemStatus,
  listDatasetsStatus,
  listEvents,
  listProviders,
  listTasks,
  type DatasetStatus,
  type EventRecord,
  type Provider,
  type SystemStatus,
  type TaskHandle,
} from "../api";
import { RetryTaskButton } from "../components/TaskActions";
import {
  ConnectionWarning,
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
}

type AttentionItem =
  | { type: "task"; value: TaskHandle }
  | { type: "event"; value: EventRecord }
  | { type: "cron"; value: EventRecord }
  | { type: "provider"; value: Provider }
  | { type: "dataset"; value: DatasetStatus };

const MAX_HOME_ITEMS = 4;

function isActive(task: TaskHandle): boolean {
  return !TERMINAL_STATUSES.has(task.status);
}

async function loadHome(): Promise<HomeData> {
  const [status, providers, events, tasks, datasets] = await Promise.all([
    getSystemStatus(),
    listProviders(),
    listEvents({ unread: true }),
    listTasks(),
    listDatasetsStatus(),
  ]);
  return {
    status,
    providers: providers.items,
    unreadEvents: events.items.filter((event) => !event.acknowledged),
    tasks: tasks.items,
    datasets: datasets.items,
  };
}

function AttentionRow({
  item,
  acknowledge,
  dismiss,
}: {
  item: AttentionItem;
  acknowledge: (eventId: string) => void;
  dismiss: (eventId: string) => void;
}) {
  if (item.type === "task") {
    const task = item.value;
    return (
      <div className="attention-row">
        <span className="badge severity-error">Failed task</span>
        <span className="attention-message">
          <Link to={`/tasks/${encodeURIComponent(task.handle_id)}`} className="mono">
            {task.dataset} {task.operation}
          </Link>{" "}
          <span className="muted">{task.reason ?? task.error ?? ""}</span>
        </span>
        <span className="row-actions">
          <Link to={`/tasks/${encodeURIComponent(task.handle_id)}`}>Explain</Link>
          <RetryTaskButton task={task} />
        </span>
      </div>
    );
  }

  if (item.type === "provider") {
    const provider = item.value;
    return (
      <div className="attention-row">
        <span className="badge severity-warning">Provider</span>
        <span className="attention-message">
          <span className="mono">{provider.name}</span> <span className="muted">needs configuration</span>
        </span>
        <Link to="/providers">Fix provider</Link>
      </div>
    );
  }

  if (item.type === "dataset") {
    const dataset = item.value;
    return (
      <div className="attention-row">
        <span className="badge severity-warning">Dataset</span>
        <span className="attention-message">
          <span className="mono">{dataset.name}</span> <span className="muted">has data but update is blocked</span>
        </span>
        <Link to={`/datasets/${encodeURIComponent(dataset.name)}?tab=settings`}>Open settings</Link>
      </div>
    );
  }

  const event = item.value;
  const isCron = item.type === "cron";
  return (
    <div className="attention-row">
      {isCron ? <span className="badge severity-warning">Missed cron</span> : <SeverityBadge severity={event.severity} />}
      <span className="attention-message">
        {event.message} {!isCron && <span className="muted"><Time unix={event.timestamp} /></span>}
      </span>
      <span className="row-actions">
        <button className="link-button" onClick={() => acknowledge(event.event_id)}>Acknowledge</button>
        <button className="link-button" onClick={() => dismiss(event.event_id)}>Dismiss</button>
        {isCron && <Link to="/cron">Open cron</Link>}
      </span>
    </div>
  );
}

export default function HomePage() {
  const [hasActive, setHasActive] = useState(false);
  const [dismissedEvents, setDismissedEvents] = useState<Set<string>>(new Set());
  const loader = useCallback(async () => {
    const data = await loadHome();
    setHasActive(data.tasks.some(isActive));
    return data;
  }, []);
  const live = useLiveData(loader, hasActive ? 2_500 : 12_000);
  const { data } = live;

  const acknowledge = useCallback((eventId: string) => {
    void ackEvent({ event_id: eventId });
    setDismissedEvents((previous) => new Set(previous).add(eventId));
  }, []);
  const dismiss = useCallback((eventId: string) => {
    setDismissedEvents((previous) => new Set(previous).add(eventId));
  }, []);

  if (!data && !live.error) return <Loading />;

  const failed = data?.tasks.filter((task) => task.status === "failed") ?? [];
  const problemEvents =
    data?.unreadEvents.filter(
      (event) =>
        !dismissedEvents.has(event.event_id) &&
        (event.severity === "warning" || event.severity === "error") &&
        event.kind !== "cron_missed",
    ) ?? [];
  const cronMissed =
    data?.unreadEvents.filter(
      (event) => !dismissedEvents.has(event.event_id) && event.kind === "cron_missed",
    ) ?? [];
  const badProviders = data?.providers.filter((provider) => !provider.ready || provider.configured === false) ?? [];
  const staleDatasets = data?.datasets.filter((dataset) => dataset.state === "ready" && !dataset.update_ready) ?? [];
  const attention = [
    ...failed.map((value): AttentionItem => ({ type: "task", value })),
    ...problemEvents.map((value): AttentionItem => ({ type: "event", value })),
    ...cronMissed.map((value): AttentionItem => ({ type: "cron", value })),
    ...badProviders.map((value): AttentionItem => ({ type: "provider", value })),
    ...staleDatasets.map((value): AttentionItem => ({ type: "dataset", value })),
  ];
  const liveTasks = data?.tasks.filter(isActive) ?? [];
  const initialized = data?.datasets.filter((dataset) => dataset.state === "ready").length ?? 0;
  const runnable = data?.datasets.filter((dataset) => dataset.update_ready).length ?? 0;

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
          <section className="home-overview">
            <div className="home-overview-copy">
              <p className="home-eyebrow">Workspace overview</p>
              <h2>{attention.length === 0 ? "Everything is on track" : "A few things need attention"}</h2>
              <p className="muted">
                {attention.length === 0
                  ? "Your providers and datasets are ready for their next update."
                  : "Resolve the items below, or browse datasets to run an update."}
              </p>
              <div className="home-overview-actions">
                <Link className="btn btn-primary" to="/datasets">Browse datasets</Link>
                <Link className="btn btn-secondary" to="/cron">View schedules</Link>
              </div>
            </div>
            <div className="home-metrics">
              <Link to="/datasets" className="home-metric"><strong>{data.datasets.length}</strong><span>datasets</span></Link>
              <Link to="/datasets" className="home-metric"><strong>{runnable}</strong><span>ready to update</span></Link>
              <Link to={attention.length > 0 ? "/events" : "/datasets"} className="home-metric"><strong>{attention.length}</strong><span>need attention</span></Link>
              <Link to="/datasets" className="home-metric"><strong>{initialized}</strong><span>with data</span></Link>
            </div>
          </section>

          {attention.length > 0 && (
            <section className="home-section">
              <div className="home-section-head"><h2>Needs attention</h2><Link to="/events">View all</Link></div>
              <div className="attention-list">
                {attention.slice(0, MAX_HOME_ITEMS).map((item, index) => (
                  <AttentionRow key={`${item.type}-${index}`} item={item} acknowledge={acknowledge} dismiss={dismiss} />
                ))}
              </div>
            </section>
          )}

          {liveTasks.length > 0 && (
            <section className="home-section">
              <div className="home-section-head"><h2>Running now</h2><Link to="/tasks">View tasks</Link></div>
              <div className="live-list">
                {liveTasks.slice(0, MAX_HOME_ITEMS).map((task) => (
                  <div key={task.handle_id} className="live-row">
                    <StatusBadge status={task.status} />
                    <Link to={`/tasks/${encodeURIComponent(task.handle_id)}`} className="mono">{task.dataset} {task.operation}</Link>
                    {task.stage && <span className="muted">{task.stage}</span>}
                    <ProgressBar progress={task.progress} />
                  </div>
                ))}
              </div>
            </section>
          )}

          <div className="home-server-link muted">
            Server is {data.status.status}. <Link to="/server">View server details →</Link>
          </div>
        </>
      )}
    </div>
  );
}
