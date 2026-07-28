import type { OperationDescription } from "./api";

/**
 * Pure mapping from an operation's JSON-schema-ish `properties` description
 * to a flat form-field model, and from entered values back to operands.
 * Kept UI-free so it can be unit tested directly.
 */

export type FieldKind = "array" | "date-range" | "text";

export interface FieldModel {
  name: string;
  kind: FieldKind;
  required: boolean;
  help?: string;
  default?: unknown;
}

/** Raw per-field input state. `text` is used for array/text fields,
 *  `from`/`to` for half-open-date-range fields. */
export interface FieldState {
  text: string;
  from: string;
  to: string;
}

export const EMPTY_FIELD: FieldState = { text: "", from: "", to: "" };

export function fieldsForOperation(op: OperationDescription): FieldModel[] {
  const required = new Set(op.required ?? []);
  return Object.entries(op.properties ?? {}).map(([name, schema]) => ({
    name,
    required: required.has(name),
    help: schema.help,
    default: schema.default,
    kind:
      schema.type === "array"
        ? "array"
        : schema.format === "half-open-date-range"
          ? "date-range"
          : "text",
  }));
}

/** Converts schema defaults into the same state used by editable fields. */
export function defaultFieldValues(
  fields: FieldModel[],
  now: Date = new Date(),
): Record<string, FieldState> {
  const formatDate = (value: Date): string => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);

  return Object.fromEntries(
    fields.flatMap((field) => {
      if (field.kind === "array" && Array.isArray(field.default)) {
        return [[field.name, { ...EMPTY_FIELD, text: field.default.map(String).join("\n") }]];
      }
      if (field.kind === "text" && typeof field.default === "string") {
        return [[field.name, { ...EMPTY_FIELD, text: field.default }]];
      }
      if (field.kind === "date-range") {
        return [[field.name, { ...EMPTY_FIELD, from: `${now.getFullYear()}-01-01`, to: formatDate(tomorrow) }]];
      }
      return [];
    }),
  );
}

/**
 * Builds the operands object from entered values.
 * - array fields: one value per textarea line -> string[] (blank lines dropped)
 * - date-range fields: `from` + `to` combined as `YYYY-MM-DD:YYYY-MM-DD`
 * - text fields: trimmed string
 * Empty optional fields are omitted. Throws an Error naming the missing
 * fields when a required field is empty (or a date range is half filled).
 */
export function buildOperands(
  fields: FieldModel[],
  values: Record<string, FieldState>,
): Record<string, unknown> {
  const operands: Record<string, unknown> = {};
  const missing: string[] = [];

  for (const field of fields) {
    const state = values[field.name] ?? EMPTY_FIELD;
    if (field.kind === "array") {
      const items = state.text
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      if (items.length === 0) {
        if (field.required) missing.push(field.name);
        continue;
      }
      operands[field.name] = items;
    } else if (field.kind === "date-range") {
      if (!state.from && !state.to) {
        if (field.required) missing.push(field.name);
        continue;
      }
      if (!state.from || !state.to) {
        missing.push(`${field.name} (needs both from and to)`);
        continue;
      }
      operands[field.name] = `${state.from}:${state.to}`;
    } else {
      const value = state.text.trim();
      if (!value) {
        if (field.required) missing.push(field.name);
        continue;
      }
      operands[field.name] = value;
    }
  }

  if (missing.length > 0) {
    throw new Error(`Missing required fields: ${missing.join(", ")}`);
  }
  return operands;
}
