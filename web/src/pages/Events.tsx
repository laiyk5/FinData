import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ackEvent, errorMessage, listEvents, type EventRecord } from "../api";
import { eventActions } from "../eventActions";
import { useToast } from "../components/Toast";
import {
  ConnectionWarning,
  EmptyState,
  FreshnessNote,
  JsonBlock,
  KvChips,
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

  if (!live.data && !live.error) return <Loading />;

  const items = live.data ?? [];
  const kinds = [...new Set(items.map((e) => e.kind))].sort();
  const visible = items.filter(
    (e) =>
      (!severity || e.severity === severity) &&
      (!kind || e.kind === kind) &&
      (!unreadOnly || !e.acknowledged) &&
      (!datasetFilter || e.context?.dataset === datasetFilter),
  );
  const unreadCount = items.filter((e) => !e.acknowledged).length;

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
      <div className="filters">
        {datasetFilter && (
          <>
            <span className="chip">
              dataset: <span className="mono">{datasetFilter}</span>
            </span>
            <button className="btn btn-xs" onClick={clearDatasetFilter}>
              Clear filter
            </button>
          </>
        )}
        <label>
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
          />
          unread only
        </label>
        <label>
          severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">all</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">all</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label>
          since
          <select value={since} onChange={(e) => setSince(e.target.value)}>
            {SINCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn"
          disabled={busy !== null || unreadCount === 0}
          onClick={() => void ack({ all: true }, "all")}
        >
          {busy === "all" ? "Acking…" : "Ack all"}
        </button>
      </div>
      <ConnectionWarning error={live.error} />
      {visible.length === 0 && (
        <EmptyState>
          No events match the filters — new task failures, cron notices, and liveness
          escalations appear here.
        </EmptyState>
      )}
      {visible.length > 0 && (
        <div className="event-list">
          {visible.map((e) => (
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
                  {Object.keys(e.context ?? {}).length > 0 && (
                    <>
                      <KvChips value={e.context} />
                      <JsonBlock value={e.context} label="raw context" />
                    </>
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
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
