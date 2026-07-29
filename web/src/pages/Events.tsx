import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ackEvent, errorMessage, listEvents, purgeAcknowledgedEvents, type EventRecord } from "../api";
import { eventActions } from "../eventActions";
import { useToast } from "../components/Toast";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  JsonBlock,
  Loading,
  Time,
} from "../components/common";
import { AlertIcon, InfoIcon } from "../components/icons";
import { useLiveData } from "../hooks";

const SINCE_OPTIONS: { value: string; label: string; hours: number }[] = [
  { value: "", label: "all time", hours: 0 },
  { value: "1h", label: "last 1h", hours: 1 },
  { value: "24h", label: "last 24h", hours: 24 },
  { value: "7d", label: "last 7d", hours: 168 },
];

const SEVERITIES = ["info", "warning", "error"] as const;

export default function EventsPage() {
  const { notify } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const datasetFilter = searchParams.get("dataset") ?? "";
  const [unreadOnly, setUnreadOnly] = useState(searchParams.has("unread"));
  const [severity, setSeverity] = useState("");
  const [kind, setKind] = useState("");
  const [since, setSince] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [hasUnread, setHasUnread] = useState(false);

  const loader = useCallback(async () => {
    const hours = SINCE_OPTIONS.find((o) => o.value === since)?.hours ?? 0;
    const r = await listEvents({
      unread: unreadOnly || undefined,
      since: hours > 0 ? Date.now() / 1000 - hours * 3600 : undefined,
    });
    setHasUnread(r.items.some((e) => !e.acknowledged));
    return r.items;
  }, [unreadOnly, since]);

  // Medium cadence while unread events exist, slow when settled.
  const live = useLiveData<EventRecord[]>(loader, hasUnread ? 3_000 : 12_000);

  const ack = async (body: { event_id: string } | { all: true }, key: string): Promise<void> => {
    setBusy(key);
    try {
      const r = await ackEvent(body);
      notify("success", `acknowledged ${r.acknowledged} event${r.acknowledged === 1 ? "" : "s"}`);
      await live.refresh();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const purge = async (body: { event_id: string } | { all: true }, key: string): Promise<void> => {
    setBusy(key);
    try {
      const result = await purgeAcknowledgedEvents(body);
      notify("success", `removed ${result.purged} acknowledged event${result.purged === 1 ? "" : "s"}`);
      await live.refresh();
    } catch (err) {
      notify("error", errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (!live.data && !live.error) return <Loading />;

  const items = live.data ?? [];
  const kinds = [...new Set(items.map((e) => e.kind))].sort();
  const visible = items.filter(
    (event) => {
      const searchable = [
        event.message,
        event.kind,
        typeof event.context?.dataset === "string" ? event.context.dataset : "",
      ]
        .join(" ")
        .toLowerCase();
      return (
        (!severity || event.severity === severity) &&
        (!kind || event.kind === kind) &&
        (!unreadOnly || !event.acknowledged) &&
        (!datasetFilter || event.context?.dataset === datasetFilter) &&
        (!query.trim() || searchable.includes(query.trim().toLowerCase()))
      );
    },
  );
  const unreadCount = items.filter((e) => !e.acknowledged).length;
  const acknowledgedCount = items.length - unreadCount;

  const clearDatasetFilter = (): void => {
    const next: Record<string, string> = {};
    if (unreadOnly) next.unread = "";
    setSearchParams(next);
  };

  return (
    <div>
      <div className="page-head">
        <h1>Events</h1>
        <FreshnessNote lastUpdated={live.lastUpdated} />
      </div>
      <section className="events-toolbar" aria-label="Filter events">
        <div className="events-search-row">
          <label className="events-search">
            <span>Find events</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Message, dataset, or event type"
            />
          </label>
          <span className="events-result-count">
            {visible.length} event{visible.length === 1 ? "" : "s"} · {unreadCount} open
          </span>
          <button
            className="btn btn-primary"
            disabled={busy !== null || unreadCount === 0}
            onClick={() => void ack({ all: true }, "all")}
          >
            {busy === "all" ? "Acknowledging…" : "Acknowledge all"}
          </button>
          <button
            className="btn"
            disabled={busy !== null || acknowledgedCount === 0}
            onClick={() => void purge({ all: true }, "purge-all")}
          >
            {busy === "purge-all" ? "Removing…" : "Remove acknowledged"}
          </button>
        </div>
        <div className="events-filter-row">
          <div className="filter-chips" aria-label="Event state filter">
            <button
              type="button"
              className={`filter-chip ${!unreadOnly ? "active" : ""}`}
              onClick={() => setUnreadOnly(false)}
            >
              All events
            </button>
            <button
              type="button"
              className={`filter-chip ${unreadOnly ? "active" : ""}`}
              onClick={() => setUnreadOnly(true)}
            >
              Open {unreadCount > 0 && `(${unreadCount})`}
            </button>
          </div>
          <div className="filter-chips" aria-label="Event severity filter">
            <button
              type="button"
              className={`filter-chip ${severity === "" ? "active" : ""}`}
              onClick={() => setSeverity("")}
            >
              All severities
            </button>
            {SEVERITIES.map((value) => (
              <button
                key={value}
                type="button"
                className={`filter-chip ${severity === value ? "active" : ""}`}
                onClick={() => setSeverity(value)}
              >
                {value}
              </button>
            ))}
          </div>
          <label className="events-select">
            <span>Type</span>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="">all</option>
              {kinds.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="events-select">
            <span>Time</span>
            <select value={since} onChange={(event) => setSince(event.target.value)}>
              {SINCE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>
        {datasetFilter && (
          <div className="events-dataset-filter">
            <span className="chip">
              dataset: <span className="mono">{datasetFilter}</span>
            </span>
            <button className="btn btn-xs" onClick={clearDatasetFilter}>
              Clear filter
            </button>
          </div>
        )}
      </section>
      <ConnectionWarning error={live.error} />
      {visible.length === 0 && (
        <EmptyState>
          No events match the filters — new task failures, cron notices, and liveness
          escalations appear here.
        </EmptyState>
      )}
      {visible.length > 0 && (
        <div className="event-list">
          {visible
            .slice()
            .sort((left, right) => Number(left.acknowledged) - Number(right.acknowledged))
            .map((e) => (
            <div
              key={e.event_id}
              className={`event-row event-severity-${e.severity} ${e.acknowledged ? "event-acked" : ""}`}
            >
              <span className="event-icon">
                {e.severity === "info" ? <InfoIcon /> : <AlertIcon />}
              </span>
              <div className="event-body">
                <div className="event-message">{e.message}</div>
                <div className="event-meta">
                  <span className="chip chip-kind mono">{e.kind}</span>
                  {typeof e.context?.dataset === "string" && (
                    <Link
                      to={`/datasets/${encodeURIComponent(e.context.dataset)}`}
                      className="chip mono"
                    >
                      {e.context.dataset}
                    </Link>
                  )}
                  {Object.keys(e.context ?? {}).length > 0 && (
                    <details className="event-context">
                      <summary>details</summary>
                      <JsonBlock value={e.context} label="raw context" />
                    </details>
                  )}
                </div>
              </div>
              <div className="event-side">
                <span className="muted event-time">
                  <Time unix={e.timestamp} />
                </span>
                <span className="event-actions">
                  {eventActions(e).map((a) => (
                    <Link key={a.to} to={a.to} className="event-action">
                      {a.label}
                    </Link>
                  ))}
                  {!e.acknowledged && (
                    <button
                      className="btn btn-xs"
                      disabled={busy !== null}
                      onClick={() => void ack({ event_id: e.event_id }, e.event_id)}
                    >
                      {busy === e.event_id ? "…" : "Ack"}
                    </button>
                  )}
                  {e.acknowledged && (
                    <button
                      className="btn btn-xs"
                      disabled={busy !== null}
                      onClick={() => void purge({ event_id: e.event_id }, `purge:${e.event_id}`)}
                    >
                      {busy === `purge:${e.event_id}` ? "…" : "Remove"}
                    </button>
                  )}
                </span>
              </div>
            </div>
            ))}
        </div>
      )}
    </div>
  );
}
