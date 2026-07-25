import { useMemo, useState } from "react";
import { TimezoneInput, availableTimezones, isValidTimezone } from "./TimezoneInput";

/**
 * Schema-typed value editor shared by dataset settings and the Config page.
 * Rendering derives entirely from the key's server-declared `schema` (type
 * and `format`), `secret`, `required`, and `default` — never from the key's
 * name. The transport type stays JSON, but the user edits typed controls:
 * arrays as one-per-line text, booleans as a switch, numbers as a number
 * input, `format: "iana-timezone"` as a timezone picker, strings as text.
 * Secrets additionally support an `{env: VAR}` reference mode and are never
 * displayed.
 */

type EditorKind = "array" | "boolean" | "number" | "text" | "json" | "timezone";

function kindFor(schema: Record<string, unknown>): EditorKind {
  const type = (schema as { type?: unknown }).type;
  const format = (schema as { format?: unknown }).format;
  if (format === "iana-timezone") return "timezone";
  if (type === "array") return "array";
  if (type === "boolean") return "boolean";
  if (type === "number" || type === "integer") return "number";
  if (type === "object") return "json";
  return "text";
}

/** Renders a stored (non-secret) value for display. */
export function renderStoredValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value !== null && typeof value === "object" && "env" in value) {
    return `env:${String((value as { env: unknown }).env)}`;
  }
  return JSON.stringify(value);
}

export interface SettingEditorProps {
  schema: Record<string, unknown>;
  configured: boolean;
  secret?: boolean;
  /** Server-declared classification; undefined renders no marker. */
  required?: boolean;
  /** Server-declared effective default, shown when unconfigured. */
  defaultValue?: unknown;
  /** Stored value as reported by the server (secrets arrive redacted). */
  currentValue?: unknown;
  hasCurrentValue?: boolean;
  allowEnvRef?: boolean;
  /** Must throw on failure; on success the draft is cleared. */
  onSet: (value: unknown) => Promise<void>;
  /** Caller is responsible for confirmation. */
  onUnset: () => Promise<void>;
}

export function SettingEditor({
  schema,
  configured,
  secret = false,
  required,
  defaultValue,
  currentValue,
  hasCurrentValue = false,
  allowEnvRef = false,
  onSet,
  onUnset,
}: SettingEditorProps) {
  const kind = kindFor(schema);
  const zones = useMemo(availableTimezones, []);
  const [text, setText] = useState("");
  const [boolValue, setBoolValue] = useState(false);
  const [envMode, setEnvMode] = useState(secret);
  const [busy, setBusy] = useState<"set" | "unset" | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  // Placeholders carry the declared default, else the (non-secret) current value.
  const placeholderValue =
    defaultValue !== undefined
      ? renderStoredValue(defaultValue)
      : hasCurrentValue && !secret
        ? renderStoredValue(currentValue)
        : undefined;

  const buildValue = (): unknown => {
    if (envMode && allowEnvRef) {
      const variable = text.trim();
      if (!variable) throw new Error("environment variable name is required");
      return { env: variable };
    }
    if (kind === "array") {
      const items = text
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      if (items.length === 0) throw new Error("enter at least one line");
      return items;
    }
    if (kind === "boolean") return boolValue;
    if (kind === "number") {
      const trimmed = text.trim();
      if (!trimmed) throw new Error("enter a number");
      const n = Number(trimmed);
      if (!Number.isFinite(n)) throw new Error(`"${trimmed}" is not a number`);
      return n;
    }
    if (kind === "json") {
      const trimmed = text.trim();
      if (!trimmed) throw new Error("enter a JSON value");
      return JSON.parse(trimmed);
    }
    if (kind === "timezone") {
      if (!isValidTimezone(zones, text)) {
        throw new Error("choose a timezone from the IANA list");
      }
      return text.trim();
    }
    if (!text.trim()) throw new Error("enter a value");
    return text;
  };

  const doSet = async (): Promise<void> => {
    setLocalError(null);
    let value: unknown;
    try {
      value = buildValue();
    } catch (err) {
      // Local feedback only — the server remains the validation authority.
      setLocalError(err instanceof Error ? err.message : String(err));
      return;
    }
    setBusy("set");
    try {
      await onSet(value);
      setText("");
      setBoolValue(false);
    } catch {
      // The caller surfaces the server's error; keep the draft for editing.
    } finally {
      setBusy(null);
    }
  };

  const doUnset = async (): Promise<void> => {
    setBusy("unset");
    try {
      await onUnset();
    } catch {
      // surfaced by the caller
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="setting-editor">
      <div className="setting-state">
        <span className={`badge ${configured ? "bool-yes" : "bool-no"}`}>
          {configured ? "configured" : "not configured"}
        </span>{" "}
        {required === true && <span className="badge badge-required">required</span>}{" "}
        {required === false && <span className="badge badge-optional">optional</span>}{" "}
        {secret && <span className="badge badge-secret">secret</span>}
      </div>
      {hasCurrentValue && (
        <div className="muted setting-current-line">
          current: <span className="mono">{renderStoredValue(currentValue)}</span>
        </div>
      )}
      {!configured && defaultValue !== undefined && (
        <div className="muted setting-default">
          default: <span className="mono">{renderStoredValue(defaultValue)}</span> (used
          when unset)
        </div>
      )}
      <div className="form-row" style={{ alignItems: "center", marginTop: 4 }}>
        {allowEnvRef && (
          <select
            value={envMode ? "env" : "value"}
            onChange={(e) => setEnvMode(e.target.value === "env")}
            aria-label="value mode"
          >
            <option value="value">value</option>
            <option value="env">env reference</option>
          </select>
        )}
        {envMode && allowEnvRef ? (
          <input
            type="text"
            style={{ flex: 1, minWidth: 180 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="ENV_VAR_NAME"
            aria-label="environment variable"
          />
        ) : kind === "array" ? (
          <textarea
            style={{ flex: 1, minWidth: 220, minHeight: 64 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="one value per line"
            aria-label="array value"
          />
        ) : kind === "boolean" ? (
          <label className="switch-label">
            <input
              type="checkbox"
              checked={boolValue}
              onChange={(e) => setBoolValue(e.target.checked)}
            />
            {boolValue ? "true" : "false"}
          </label>
        ) : kind === "number" ? (
          <input
            type="number"
            style={{ flex: 1, minWidth: 140 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={placeholderValue ?? "0"}
            aria-label="number value"
          />
        ) : kind === "timezone" ? (
          <span style={{ flex: 1, minWidth: 200, display: "inline-flex" }}>
            <TimezoneInput
              value={text}
              onChange={setText}
              invalid={text.trim() !== "" && !isValidTimezone(zones, text)}
              placeholder={placeholderValue ?? "IANA timezone"}
            />
          </span>
        ) : (
          <input
            type={secret ? "password" : "text"}
            style={{ flex: 1, minWidth: 180 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              placeholderValue ?? (kind === "json" ? '{"key": "value"}' : "value")
            }
            aria-label="value"
          />
        )}
        <button
          className="btn btn-primary"
          disabled={busy !== null}
          onClick={() => void doSet()}
        >
          {busy === "set" ? "…" : "Set"}
        </button>
        <button
          className="btn"
          disabled={busy !== null || !configured}
          onClick={() => void doUnset()}
        >
          {busy === "unset" ? "…" : "Unset"}
        </button>
      </div>
      {localError && <div className="field-error">{localError}</div>}
    </div>
  );
}
