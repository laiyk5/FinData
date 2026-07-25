import { useState, type ReactNode } from "react";
import { errorMessage } from "../api";
import { datasetStateLabel, formatAbsolute, formatRelativeTime, shortId } from "../format";
import { AlertIcon, CheckIcon, InboxIcon, RefreshIcon } from "./icons";
import { useDisplayTimezone } from "./TimezoneContext";

export function Loading({ label = "loading…" }: { label?: string }) {
  return <div className="loading">{label}</div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <InboxIcon />
      </span>
      <div className="empty-text">{children}</div>
    </div>
  );
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error-banner">
      <AlertIcon />
      <span>{errorMessage(error)}</span>
    </div>
  );
}

/** Connection warning shown when a poll fails but stale data is still shown. */
export function ConnectionWarning({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="warning-banner">
      <AlertIcon />
      <span>
        connection problem — showing the last successful data ({errorMessage(error)})
      </span>
    </div>
  );
}

/** "last updated …" freshness note rendered by every polled view. */
export function FreshnessNote({ lastUpdated }: { lastUpdated: number | null }) {
  if (lastUpdated === null) return null;
  return (
    <span className="freshness muted">
      <RefreshIcon /> updated {formatRelativeTime(lastUpdated / 1000)}
    </span>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="notice">
      <CheckIcon />
      <span>{children}</span>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{status}</span>;
}

/** Dataset state badge with the unambiguous "has data" / "no data" labels. */
export function DatasetStateBadge({ state }: { state: string }) {
  return <span className={`badge state-${state}`}>{datasetStateLabel(state)}</span>;
}

/**
 * Quiet dot indicator for a RELATED object's dataset state (dependency chips,
 * cross-entity rows) — capsules belong to the owner, so related objects get
 * a dot plus a tooltip instead of a pill.
 */
export function StateDot({ state }: { state: string }) {
  return (
    <span
      className={`dot ${state === "ready" ? "dot-ok" : "dot-warn"}`}
      title={datasetStateLabel(state)}
    />
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`badge severity-${severity}`}>{severity}</span>;
}

export function BoolBadge({ value, label }: { value: boolean | undefined; label: string }) {
  return (
    <span className={`badge ${value ? "bool-yes" : "bool-no"}`}>
      {label}: {value ? "yes" : "no"}
    </span>
  );
}

/**
 * Shortened identifier that copies the full value on click, matching the
 * CLI's prefix convention. Shows a subtle "copied" confirmation.
 */
export function CopyableId({ id, len = 8 }: { id: string; len?: number }) {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    const done = (): void => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(id).then(done, done);
    } else {
      done();
    }
  };
  return (
    <button
      type="button"
      className="copy-id mono"
      title={`${id} — click to copy`}
      onClick={copy}
    >
      {copied ? "copied ✓" : shortId(id, len)}
    </button>
  );
}

/** Copyable inline code snippet (e.g. an inspection CLI command). */
export function CopyableCode({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    const done = (): void => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else {
      done();
    }
  };
  return (
    <span className="copy-code">
      <code>{text}</code>{" "}
      <button type="button" className="btn btn-xs" onClick={copy}>
        {copied ? "copied ✓" : "copy"}
      </button>
    </span>
  );
}

/**
 * Primary time display: relative, with the absolute timestamp (in the
 * workspace display timezone) on hover. Pass an explicit `timeZone` to
 * override the display zone (e.g. a cron job's own zone).
 */
export function Time({
  unix,
  mode = "relative",
  timeZone,
}: {
  unix: number;
  mode?: "relative" | "absolute";
  timeZone?: string;
}) {
  const displayTz = useDisplayTimezone();
  const zone = timeZone ?? displayTz;
  const absolute = formatAbsolute(unix, zone);
  if (mode === "absolute") return <span title={absolute}>{absolute}</span>;
  return (
    <span className="reltime" title={absolute}>
      {formatRelativeTime(unix)}
    </span>
  );
}

/** Collapsible raw-JSON view. */
export function JsonBlock({ value, label = "raw JSON" }: { value: unknown; label?: string }) {
  return (
    <details className="json-block">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

/** Labeled key/value chips for structured context (event context, etc.). */
export function KvChips({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <span className="muted">—</span>;
  return (
    <span className="chips">
      {entries.map(([k, v]) => (
        <span key={k} className="chip chip-kv" title={JSON.stringify(v)}>
          <span className="muted">{k}:</span>
          <span className="mono">{typeof v === "string" ? v : JSON.stringify(v)}</span>
        </span>
      ))}
    </span>
  );
}

export function ProgressBar({
  progress,
}: {
  progress: { current?: number; total?: number; checkpointed?: number } | null | undefined;
}) {
  if (!progress || typeof progress.total !== "number" || progress.total <= 0) return null;
  const pct = Math.min(100, ((progress.current ?? 0) / progress.total) * 100);
  const cp =
    typeof progress.checkpointed === "number"
      ? Math.min(100, (progress.checkpointed / progress.total) * 100)
      : null;
  return (
    <div className="progress" title={`checkpointed: ${progress.checkpointed ?? 0}`}>
      <div className="progress-fill" style={{ width: `${pct}%` }} />
      {cp !== null && <div className="progress-checkpoint" style={{ left: `${cp}%` }} />}
      <span className="progress-label">
        {progress.current ?? 0}/{progress.total}
      </span>
    </div>
  );
}
