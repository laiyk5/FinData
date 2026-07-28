import { describe, expect, it } from "vitest";
import type { OperationDescription } from "./api";
import { buildOperands, defaultFieldValues, fieldsForOperation } from "./operationForm";

const OP: OperationDescription = {
  name: "fetch",
  help: "Fetch bars for the given symbols.",
  required: ["symbols"],
  properties: {
    symbols: {
      type: "array",
      items: { type: "string" },
      minItems: 1,
      help: "Security codes or selectors, one per line",
      default: ["all"],
    },
    range: { type: "string", format: "half-open-date-range" },
    note: { type: "string" },
  },
};

describe("fieldsForOperation", () => {
  it("maps array schema to an array field", () => {
    const fields = fieldsForOperation(OP);
    const symbols = fields.find((f) => f.name === "symbols");
    expect(symbols).toEqual({
      name: "symbols",
      kind: "array",
      required: true,
      help: "Security codes or selectors, one per line",
      default: ["all"],
    });
  });

  it("maps schema defaults into editable field values", () => {
    expect(defaultFieldValues(fieldsForOperation(OP))).toMatchObject({
      symbols: { text: "all", from: "", to: "" },
    });
  });

  it("defaults date ranges to the current year through today", () => {
    expect(defaultFieldValues(fieldsForOperation(OP), new Date(2026, 6, 20))).toMatchObject({
      range: { text: "", from: "2026-01-01", to: "2026-07-21" },
    });
  });

  it("leaves help undefined when the property has none", () => {
    const fields = fieldsForOperation(OP);
    const range = fields.find((f) => f.name === "range");
    expect(range?.help).toBeUndefined();
  });

  it("maps half-open-date-range format to a date-range field", () => {
    const fields = fieldsForOperation(OP);
    const range = fields.find((f) => f.name === "range");
    expect(range).toEqual({ name: "range", kind: "date-range", required: false });
  });

  it("maps plain strings to text fields", () => {
    const fields = fieldsForOperation(OP);
    const note = fields.find((f) => f.name === "note");
    expect(note).toEqual({ name: "note", kind: "text", required: false });
  });

  it("produces no fields for an operation with empty properties", () => {
    expect(
      fieldsForOperation({ name: "update", required: [], properties: {} }),
    ).toEqual([]);
  });
});

describe("buildOperands", () => {
  it("splits a textarea into a trimmed string array, dropping blank lines", () => {
    const operands = buildOperands(fieldsForOperation(OP), {
      symbols: { text: " 000001.SZ\n\n600000.SH \n", from: "", to: "" },
    });
    expect(operands).toEqual({ symbols: ["000001.SZ", "600000.SH"] });
  });

  it("combines from/to dates into a start:end string", () => {
    const operands = buildOperands(fieldsForOperation(OP), {
      symbols: { text: "000001.SZ", from: "", to: "" },
      range: { text: "", from: "2024-01-01", to: "2024-02-01" },
    });
    expect(operands).toEqual({
      symbols: ["000001.SZ"],
      range: "2024-01-01:2024-02-01",
    });
  });

  it("omits empty optional fields", () => {
    const operands = buildOperands(fieldsForOperation(OP), {
      symbols: { text: "000001.SZ", from: "", to: "" },
      note: { text: "   ", from: "", to: "" },
    });
    expect(operands).toEqual({ symbols: ["000001.SZ"] });
  });

  it("blocks submission when a required field is empty", () => {
    expect(() =>
      buildOperands(fieldsForOperation(OP), {
        symbols: { text: "\n  \n", from: "", to: "" },
      }),
    ).toThrow(/symbols/);
  });

  it("blocks a half-filled date range", () => {
    expect(() =>
      buildOperands(fieldsForOperation(OP), {
        symbols: { text: "000001.SZ", from: "", to: "" },
        range: { text: "", from: "2024-01-01", to: "" },
      }),
    ).toThrow(/range/);
  });
});
