import { formatClock } from "./format";

/**
 * Pure mapping from a raw task-log entry (typed record or legacy bare
 * string) to a render-ready line model. Kept UI-free so it can be unit
 * tested directly (see operationForm.ts for the same pattern).
 */

export interface LogLineView {
  /** Local `HH:MM:SS` prefix when the entry carries a timestamp, else null. */
  timestamp: string | null;
  text: string;
  /** Diagnostic severity ("warning" | "error" | other), null for plain lines. */
  severity: string | null;
}

export interface DedupedLogLine {
  view: LogLineView;
  /** Number of consecutive identical rendered lines collapsed into this one. */
  count: number;
}

/**
 * Collapses runs of consecutive entries whose rendered text and severity are
 * identical into one line carrying a repeat count (mirrors the CLI's "exact
 * repeats may be combined with an occurrence count"). The first occurrence's
 * timestamp is kept.
 */
export function dedupLogLines(items: unknown[]): DedupedLogLine[] {
  const out: DedupedLogLine[] = [];
  for (const item of items) {
    const view = logLineView(item);
    const last = out[out.length - 1];
    if (last && last.view.text === view.text && last.view.severity === view.severity) {
      last.count += 1;
    } else {
      out.push({ view, count: 1 });
    }
  }
  return out;
}

export function logLineView(item: unknown): LogLineView {
  if (typeof item === "string") {
    return { timestamp: null, text: item, severity: null };
  }
  if (item !== null && typeof item === "object") {
    const rec = item as Record<string, unknown>;
    const timestamp =
      typeof rec.time === "number" ? formatClock(rec.time) : null;
    if (rec.type === "log" || rec.type === "task.log") {
      return { timestamp, text: String(rec.message ?? ""), severity: null };
    }
    if (rec.type === "task.diagnostic") {
      const severity = String(rec.severity ?? "info");
      const count =
        typeof rec.count === "number" && rec.count > 1 ? ` (×${rec.count})` : "";
      return {
        timestamp,
        text: `[${severity}] ${String(rec.code ?? "")}: ${String(rec.message ?? "")}${count}`,
        severity,
      };
    }
  }
  return { timestamp: null, text: JSON.stringify(item), severity: null };
}
