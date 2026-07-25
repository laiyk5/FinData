import { useMemo, useState } from "react";
import {
  CRON_FIELD_SPECS,
  draftFromExpression,
  expressionFromDraft,
  humanizeCron,
  validateCronField,
  type CronDraft,
  type CronMode,
} from "../cron";
import { TimezoneInput, availableTimezones, isValidTimezone } from "./TimezoneInput";

const MODES: { value: CronMode; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "weekdays", label: "Weekdays (Mon–Fri)" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom" },
];

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

/**
 * Guided cron schedule editor: preset modes with time/day pickers, a custom
 * mode with per-field validated inputs, an IANA timezone selector, and a live
 * humanized preview. Illegal expressions never reach the API; the server
 * remains the validation authority.
 */
export function CronEditor({
  expression,
  timezone,
  busy,
  onSave,
  onCancel,
}: {
  expression: string;
  timezone: string;
  busy: boolean;
  onSave: (expression: string, timezone: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<CronDraft>(() => draftFromExpression(expression));
  const [tz, setTz] = useState(timezone);
  const zones = useMemo(availableTimezones, []);

  const patch = (p: Partial<CronDraft>): void => setDraft((d) => ({ ...d, ...p }));
  const patchField = (i: number, value: string): void =>
    setDraft((d) => {
      const fields = [...d.fields] as CronDraft["fields"];
      fields[i] = value;
      return { ...d, fields };
    });

  const expr = expressionFromDraft(draft);
  const fieldErrors =
    draft.mode === "custom"
      ? draft.fields.map((f, i) => {
          const spec = CRON_FIELD_SPECS[i];
          return validateCronField(f, spec.min, spec.max);
        })
      : [];
  const tzValid = isValidTimezone(zones, tz);
  const canSave = expr !== null && tzValid && !busy;

  return (
    <div className="cron-editor">
      <div className="form-row" style={{ alignItems: "center" }}>
        <label className="field" style={{ marginBottom: 0 }}>
          <span className="field-label">schedule</span>
          <select
            value={draft.mode}
            onChange={(e) => patch({ mode: e.target.value as CronMode })}
          >
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>

        {draft.mode !== "custom" && (
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="field-label">time</span>
            <input
              type="time"
              value={draft.time}
              onChange={(e) => patch({ time: e.target.value })}
            />
          </label>
        )}

        {draft.mode === "weekly" && (
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="field-label">weekday</span>
            <select
              value={draft.weekday}
              onChange={(e) => patch({ weekday: Number(e.target.value) })}
            >
              {WEEKDAYS.map((name, i) => (
                <option key={i} value={i}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}

        {draft.mode === "monthly" && (
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="field-label">day of month</span>
            <select
              value={draft.dayOfMonth}
              onChange={(e) => patch({ dayOfMonth: Number(e.target.value) })}
            >
              {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="field" style={{ marginBottom: 0 }}>
          <span className="field-label">timezone</span>
          <TimezoneInput value={tz} onChange={setTz} invalid={!tzValid && tz.trim() !== ""} />
        </label>
      </div>

      {draft.mode === "monthly" && (
        <p className="muted cron-editor-note">
          Days 1–28 keep the schedule valid in every month.
        </p>
      )}

      {draft.mode === "custom" && (
        <div className="cron-custom-fields">
          {draft.fields.map((value, i) => (
            <label key={i} className="field" style={{ marginBottom: 0 }}>
              <span className="field-label">{CRON_FIELD_SPECS[i].name}</span>
              <input
                type="text"
                className="mono"
                value={value}
                onChange={(e) => patchField(i, e.target.value)}
                aria-invalid={fieldErrors[i] !== null}
              />
              {fieldErrors[i] && <span className="field-error">{fieldErrors[i]}</span>}
            </label>
          ))}
        </div>
      )}

      {!tzValid && tz.trim() !== "" && (
        <div className="field-error">choose a timezone from the IANA list</div>
      )}

      <div className="cron-preview">
        {expr !== null ? (
          <>
            <span>{humanizeCron(expr)}</span> <code className="muted">{expr}</code>{" "}
            <span className="muted">({tzValid ? tz : "no timezone selected"})</span>
          </>
        ) : (
          <span className="warning-text">fix the fields above to build a valid schedule</span>
        )}
      </div>

      <div className="form-row">
        <button
          className="btn btn-primary"
          disabled={!canSave}
          onClick={() => expr !== null && onSave(expr, tz)}
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
