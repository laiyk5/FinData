import { describe, expect, it } from "vitest";
import { hasFiveFields, humanizeCron, validateCronField, expressionFromDraft, draftFromExpression } from "./cron";

describe("humanizeCron", () => {
  it("renders a specific time on one weekday", () => {
    expect(humanizeCron("0 9 * * 1")).toBe("At 09:00, every Monday");
  });

  it("treats both 0 and 7 as Sunday", () => {
    expect(humanizeCron("30 8 * * 0")).toBe("At 08:30, every Sunday");
    expect(humanizeCron("30 8 * * 7")).toBe("At 08:30, every Sunday");
  });

  it("collapses a weekday range", () => {
    expect(humanizeCron("40 17 * * 1-5")).toBe("At 17:40, Monday–Friday");
  });

  it("renders comma-separated weekdays", () => {
    expect(humanizeCron("0 9 * * 1,3,5")).toBe(
      "At 09:00, on Monday, Wednesday, Friday",
    );
  });

  it("renders every day when the day fields are wildcards", () => {
    expect(humanizeCron("0 6 * * *")).toBe("At 06:00, every day");
  });

  it("renders minute steps", () => {
    expect(humanizeCron("*/15 * * * *")).toBe("Every 15 minutes");
    expect(humanizeCron("* * * * *")).toBe("Every minute");
  });

  it("renders hour steps", () => {
    expect(humanizeCron("0 */6 * * *")).toBe("Every 6 hours at minute 0");
  });

  it("renders multiple hours", () => {
    expect(humanizeCron("30 9,17 * * *")).toBe("At 09:30, 17:30, every day");
  });

  it("renders day-of-month schedules", () => {
    expect(humanizeCron("0 9 1 * *")).toBe("At 09:00, on day 1 of every month");
    expect(humanizeCron("0 9 1 3 *")).toBe("At 09:00, on March 1");
  });

  it("falls back to the raw expression for unrecognized shapes", () => {
    expect(humanizeCron("0 9 * JAN *")).toBe("0 9 * JAN *");
    expect(humanizeCron("0 9 * *")).toBe("0 9 * *");
    expect(humanizeCron("0 9 * * 8")).toBe("0 9 * * 8");
    expect(humanizeCron("@daily")).toBe("@daily");
  });
});

describe("hasFiveFields", () => {
  it("accepts five non-empty fields", () => {
    expect(hasFiveFields("0 9 * * 1")).toBe(true);
    expect(hasFiveFields("  */15   *  * * *  ")).toBe(true);
  });

  it("rejects other shapes", () => {
    expect(hasFiveFields("0 9 * *")).toBe(false);
    expect(hasFiveFields("0 9 * * * *")).toBe(false);
    expect(hasFiveFields("")).toBe(false);
  });
});

describe("validateCronField", () => {
  it("accepts wildcard, steps, numbers, lists, and ranges", () => {
    expect(validateCronField("*", 0, 59)).toBeNull();
    expect(validateCronField("*/15", 0, 59)).toBeNull();
    expect(validateCronField("0", 0, 59)).toBeNull();
    expect(validateCronField("59", 0, 59)).toBeNull();
    expect(validateCronField("1,2,3", 0, 59)).toBeNull();
    expect(validateCronField("1-5", 0, 7)).toBeNull();
  });

  it("rejects out-of-range and malformed content", () => {
    expect(validateCronField("60", 0, 59)).toMatch(/outside/);
    expect(validateCronField("5-1", 0, 59)).toMatch(/outside/);
    expect(validateCronField("", 0, 59)).toMatch(/empty/);
    expect(validateCronField("*/0", 0, 59)).toMatch(/step/);
    expect(validateCronField("mon", 0, 7)).toMatch(/mon/);
    expect(validateCronField("8", 0, 7)).toMatch(/outside/);
  });
});

describe("expressionFromDraft", () => {
  const base = { weekday: 1, dayOfMonth: 1, fields: ["0", "9", "*", "*", "*"] as [string, string, string, string, string] };

  it("generates preset expressions", () => {
    expect(expressionFromDraft({ ...base, mode: "daily", time: "09:00" })).toBe("0 9 * * *");
    expect(expressionFromDraft({ ...base, mode: "weekdays", time: "17:40" })).toBe("40 17 * * 1-5");
    expect(expressionFromDraft({ ...base, mode: "weekly", time: "08:30", weekday: 5 })).toBe("30 8 * * 5");
    expect(expressionFromDraft({ ...base, mode: "monthly", time: "06:00", dayOfMonth: 15 })).toBe("0 6 15 * *");
  });

  it("strips leading zeros in preset expressions", () => {
    expect(expressionFromDraft({ ...base, mode: "daily", time: "07:05" })).toBe("5 7 * * *");
  });

  it("passes validated custom fields through", () => {
    expect(
      expressionFromDraft({ ...base, mode: "custom", time: "09:00", fields: ["*/15", "*", "1,15", "*", "1-5"] }),
    ).toBe("*/15 * 1,15 * 1-5");
  });

  it("returns null for invalid input", () => {
    expect(expressionFromDraft({ ...base, mode: "daily", time: "25:00" })).toBeNull();
    expect(expressionFromDraft({ ...base, mode: "daily", time: "" })).toBeNull();
    expect(
      expressionFromDraft({ ...base, mode: "custom", time: "09:00", fields: ["61", "*", "*", "*", "*"] }),
    ).toBeNull();
  });
});

describe("draftFromExpression", () => {
  it("recognizes the preset shapes", () => {
    expect(draftFromExpression("0 9 * * *")).toMatchObject({ mode: "daily", time: "09:00" });
    expect(draftFromExpression("40 17 * * 1-5")).toMatchObject({ mode: "weekdays", time: "17:40" });
    expect(draftFromExpression("30 8 * * 0")).toMatchObject({ mode: "weekly", time: "08:30", weekday: 0 });
    expect(draftFromExpression("0 6 15 * *")).toMatchObject({ mode: "monthly", time: "06:00", dayOfMonth: 15 });
  });

  it("falls back to custom with the fields as-is", () => {
    const draft = draftFromExpression("*/15 9-17 * * 1,3");
    expect(draft.mode).toBe("custom");
    expect(draft.fields).toEqual(["*/15", "9-17", "*", "*", "1,3"]);
  });

  it("treats out-of-range preset lookalikes as custom", () => {
    expect(draftFromExpression("0 9 * * 8").mode).toBe("custom");
    expect(draftFromExpression("99 9 * * *").mode).toBe("custom");
  });

  it("round-trips through expressionFromDraft", () => {
    for (const expr of ["0 9 * * *", "40 17 * * 1-5", "30 8 * * 0", "0 6 15 * *", "*/15 * 1,15 * 1-5"]) {
      expect(expressionFromDraft(draftFromExpression(expr))).toBe(expr);
    }
  });
});
