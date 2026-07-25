/** Display formatting helpers. Identifier shortening is display-only. */

const pad = (n: number): string => String(n).padStart(2, "0");

/** Unix seconds -> local `YYYY-MM-DD HH:MM:SS`. */
export function formatTime(unixSeconds: number | null | undefined): string {
  if (unixSeconds === null || unixSeconds === undefined) return "—";
  const d = new Date(unixSeconds * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Unix seconds -> local `HH:MM:SS` (24h clock). */
export function formatClock(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * Relative time for primary display ("3m ago" / "in 5m"). `nowSeconds` is
 * injectable for tests. Absolute timestamps belong in a `title` tooltip.
 */
export function formatRelativeTime(
  unixSeconds: number,
  nowSeconds: number = Date.now() / 1000,
): string {
  const diff = unixSeconds - nowSeconds;
  const past = diff <= 0;
  const abs = Math.abs(diff);
  if (abs < 5) return past ? "just now" : "now";
  const unit = (n: number, suffix: string): string =>
    past ? `${n}${suffix} ago` : `in ${n}${suffix}`;
  if (abs < 60) return unit(Math.round(abs), "s");
  if (abs < 3600) return unit(Math.round(abs / 60), "m");
  if (abs < 86400) return unit(Math.round(abs / 3600), "h");
  return unit(Math.round(abs / 86400), "d");
}

/**
 * Absolute timestamp in the workspace display timezone (`display.timezone`
 * from configuration); falls back to browser local when unset or invalid.
 */
export function formatAbsolute(unixSeconds: number, timeZone?: string): string {
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "shortOffset",
    timeZone,
  };
  try {
    return new Intl.DateTimeFormat("en-CA", options).format(unixSeconds * 1000);
  } catch {
    return formatTime(unixSeconds);
  }
}

/** Parses a server timestamp (epoch seconds or ISO string) to epoch seconds. */
export function parseServerTime(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/** Human-formatted byte count (B/KB/MB/GB, one decimal above 1 KB). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"] as const;
  let value = bytes / 1024;
  let unit: string = units[0];
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i];
  }
  return `${value.toFixed(1)} ${unit}`;
}

/**
 * Dataset state rendered unambiguously: the state only means the database has
 * committed data — never a bare "ready". Unknown states render verbatim.
 */
export function datasetStateLabel(state: string): string {
  if (state === "ready") return "has data";
  if (state === "uninitialized") return "no data";
  return state;
}

export function shortId(id: string, len = 8): string {
  return id.length <= len ? id : id.slice(0, len);
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}
