/**
 * Tiny 5-field cron humanizer. Covers the common forms (specific minute/hour,
 * `*` and `*​/n` steps, lists, ranges, day-of-week numbers 0-7); anything
 * unrecognized falls back to the raw expression. Display-only — the server
 * remains the authority on schedule validity.
 */

const DOW_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

type Field =
  | { kind: "any" }
  | { kind: "step"; step: number }
  | { kind: "values"; values: number[] }
  | null;

function parseField(field: string, min: number, max: number): Field {
  if (field === "*") return { kind: "any" };
  const stepMatch = /^\*\/(\d+)$/.exec(field);
  if (stepMatch) {
    const step = Number(stepMatch[1]);
    return step > 0 ? { kind: "step", step } : null;
  }
  const values: number[] = [];
  for (const part of field.split(",")) {
    const rangeMatch = /^(\d+)-(\d+)$/.exec(part);
    if (rangeMatch) {
      const lo = Number(rangeMatch[1]);
      const hi = Number(rangeMatch[2]);
      if (lo < min || hi > max || lo > hi) return null;
      for (let v = lo; v <= hi; v += 1) values.push(v);
      continue;
    }
    if (!/^\d+$/.test(part)) return null;
    const v = Number(part);
    if (v < min || v > max) return null;
    values.push(v);
  }
  return { kind: "values", values };
}

const pad = (n: number): string => String(n).padStart(2, "0");

const dowName = (v: number): string => DOW_NAMES[v % 7];

function describeTime(minute: Field, hour: Field): string | null {
  if (!minute || !hour) return null;
  if (minute.kind === "any" && hour.kind === "any") return "Every minute";
  if (minute.kind === "step" && hour.kind === "any") {
    return `Every ${minute.step} minutes`;
  }
  if (minute.kind === "values" && hour.kind === "step") {
    return `Every ${hour.step} hours at minute ${minute.values.join(", ")}`;
  }
  if (minute.kind === "values" && hour.kind === "values") {
    const times = hour.values.flatMap((h) =>
      minute.kind === "values" ? minute.values.map((m) => `${pad(h)}:${pad(m)}`) : [],
    );
    return `At ${times.join(", ")}`;
  }
  return null;
}

function describeDays(dom: Field, month: Field, dow: Field): string | null {
  if (!dom || !month || !dow) return null;
  if (dom.kind === "any" && month.kind === "any") {
    if (dow.kind === "any") return "every day";
    if (dow.kind === "values") {
      const days = dow.values;
      if (days.length === 1) return `every ${dowName(days[0])}`;
      // Collapse a contiguous run into a range (e.g. 1-5 -> Monday–Friday).
      const contiguous = days.every((v, i) => i === 0 || v === days[i - 1] + 1);
      if (days.length > 2 && contiguous && days[days.length - 1] !== 0) {
        return `${dowName(days[0])}–${dowName(days[days.length - 1])}`;
      }
      return `on ${days.map(dowName).join(", ")}`;
    }
    return null;
  }
  if (dom.kind === "values" && dom.values.length === 1) {
    const day = dom.values[0];
    if (month.kind === "any") return `on day ${day} of every month`;
    if (month.kind === "values" && month.values.length === 1) {
      return `on ${MONTH_NAMES[month.values[0] - 1]} ${day}`;
    }
  }
  return null;
}

/** Human summary of a 5-field cron expression, or the raw expression. */
export function humanizeCron(expression: string): string {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) return expression;
  const minute = parseField(fields[0], 0, 59);
  const hour = parseField(fields[1], 0, 23);
  const dom = parseField(fields[2], 1, 31);
  const month = parseField(fields[3], 1, 12);
  const dow = parseField(fields[4], 0, 7);
  const time = describeTime(minute, hour);
  if (time === null) return expression;
  // "Every minute(s)" already implies the day cadence.
  if (time.startsWith("Every") && dom?.kind === "any" && month?.kind === "any" && dow?.kind === "any") {
    return time;
  }
  const days = describeDays(dom, month, dow);
  if (days === null) return expression;
  return `${time}, ${days}`;
}

/** Feedback-only shape check for the schedule editor: five non-empty fields. */
export function hasFiveFields(expression: string): boolean {
  const fields = expression.trim().split(/\s+/);
  return fields.length === 5 && fields.every((f) => f.length > 0);
}

// ---------------------------------------------------------------------------
// Guided schedule editor: presets, custom-field validation, expression parsing
// ---------------------------------------------------------------------------

export const CRON_FIELD_SPECS = [
  { name: "minute", min: 0, max: 59 },
  { name: "hour", min: 0, max: 23 },
  { name: "day of month", min: 1, max: 31 },
  { name: "month", min: 1, max: 12 },
  { name: "day of week", min: 0, max: 7 },
] as const;

/**
 * Validates one custom cron field: `*`, `*​/n`, numbers, lists (`1,2,3`), and
 * ranges (`1-5`) within [min, max]. Returns an inline error message or null.
 */
export function validateCronField(value: string, min: number, max: number): string | null {
  const v = value.trim();
  if (v === "") return "field is empty";
  if (v === "*") return null;
  const step = /^\*\/(\d+)$/.exec(v);
  if (step) return Number(step[1]) > 0 ? null : "step must be at least 1";
  for (const part of v.split(",")) {
    const range = /^(\d+)-(\d+)$/.exec(part);
    if (range) {
      const lo = Number(range[1]);
      const hi = Number(range[2]);
      if (lo < min || hi > max || lo > hi) {
        return `range ${part} is outside ${min}–${max}`;
      }
      continue;
    }
    if (!/^\d+$/.test(part)) return `"${part}" — use *, */n, a number, list, or range`;
    const n = Number(part);
    if (n < min || n > max) return `${part} is outside ${min}–${max}`;
  }
  return null;
}

export type CronMode = "daily" | "weekdays" | "weekly" | "monthly" | "custom";

export interface CronDraft {
  mode: CronMode;
  /** "HH:MM" 24h, used by the preset modes. */
  time: string;
  /** 0–7 (0 and 7 are Sunday), weekly mode. */
  weekday: number;
  /** 1–31, monthly mode. */
  dayOfMonth: number;
  /** Raw per-field input, custom mode. */
  fields: [string, string, string, string, string];
}

export const DEFAULT_CRON_DRAFT: CronDraft = {
  mode: "daily",
  time: "09:00",
  weekday: 1,
  dayOfMonth: 1,
  fields: ["0", "9", "*", "*", "*"],
};

const TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/** Builds the 5-field expression for a draft, or null when it is invalid. */
export function expressionFromDraft(draft: CronDraft): string | null {
  if (draft.mode === "custom") {
    const fields = draft.fields.map((f) => f.trim());
    for (let i = 0; i < 5; i += 1) {
      const spec = CRON_FIELD_SPECS[i];
      if (validateCronField(fields[i], spec.min, spec.max) !== null) return null;
    }
    return fields.join(" ");
  }
  const m = TIME_RE.exec(draft.time);
  if (!m) return null;
  const hour = String(Number(m[1]));
  const minute = String(Number(m[2]));
  switch (draft.mode) {
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekdays":
      return `${minute} ${hour} * * 1-5`;
    case "weekly":
      return `${minute} ${hour} * * ${draft.weekday}`;
    case "monthly":
      return `${minute} ${hour} ${draft.dayOfMonth} * *`;
  }
}

const PRESET_RE = /^(\d{1,2}) (\d{1,2}) (\*|\d{1,2}) \* (\*|\d|1-5)$/;

/**
 * Parses an existing expression back into editor state: preset shapes map to
 * their mode; anything else falls back to Custom with the fields as-is.
 */
export function draftFromExpression(expression: string): CronDraft {
  const fields = expression.trim().split(/\s+/);
  const custom: CronDraft = {
    ...DEFAULT_CRON_DRAFT,
    mode: "custom",
    fields: [0, 1, 2, 3, 4].map((i) => fields[i] ?? "*") as CronDraft["fields"],
  };
  const m = PRESET_RE.exec(expression.trim());
  if (!m) return custom;
  const minute = Number(m[1]);
  const hour = Number(m[2]);
  if (minute > 59 || hour > 23) return custom;
  const time = `${pad(hour)}:${pad(minute)}`;
  if (m[3] === "*" && m[4] === "*") {
    return { ...custom, mode: "daily", time };
  }
  if (m[3] === "*" && m[4] === "1-5") {
    return { ...custom, mode: "weekdays", time };
  }
  if (m[3] === "*" && /^\d$/.test(m[4])) {
    const dow = Number(m[4]);
    if (dow <= 7) return { ...custom, mode: "weekly", time, weekday: dow };
  }
  if (m[4] === "*" && /^\d{1,2}$/.test(m[3])) {
    const dom = Number(m[3]);
    if (dom >= 1 && dom <= 31) return { ...custom, mode: "monthly", time, dayOfMonth: dom };
  }
  return custom;
}
