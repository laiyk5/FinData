import { describe, expect, it } from "vitest";
import { formatClock } from "./format";
import { dedupLogLines, logLineView } from "./logEntries";

// 2026-07-24T05:52:05.461Z — fixed epoch for timestamp expectations.
const EPOCH = 1784866602.123;

function expectedClock(epoch: number): string {
  const d = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

describe("formatClock", () => {
  it("formats epoch seconds as local HH:MM:SS (24h)", () => {
    const clock = formatClock(EPOCH);
    expect(clock).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(clock).toBe(expectedClock(EPOCH));
  });
});

describe("logLineView", () => {
  it("renders a log record with its timestamp", () => {
    const view = logLineView({
      type: "log",
      time: EPOCH,
      message: "fetch daily_basic(trade_date=20260722)",
    });
    expect(view).toEqual({
      timestamp: expectedClock(EPOCH),
      text: "fetch daily_basic(trade_date=20260722)",
      severity: null,
    });
  });

  it("renders a log record without time as a plain line", () => {
    const view = logLineView({ type: "log", message: "running" });
    expect(view).toEqual({ timestamp: null, text: "running", severity: null });
  });

  it("renders a diagnostic with a timestamp prefix", () => {
    const view = logLineView({
      type: "task.diagnostic",
      severity: "warning",
      code: "rate_limit",
      message: "provider throttled",
      context: { provider: "tushare" },
      count: 1,
      time: EPOCH,
    });
    expect(view.timestamp).toBe(expectedClock(EPOCH));
    expect(view.text).toBe("[warning] rate_limit: provider throttled");
    expect(view.severity).toBe("warning");
  });

  it("renders a diagnostic without time and folds repeat counts", () => {
    const view = logLineView({
      type: "task.diagnostic",
      severity: "error",
      code: "fetch_failed",
      message: "HTTP 500",
      context: {},
      count: 3,
    });
    expect(view.timestamp).toBeNull();
    expect(view.text).toBe("[error] fetch_failed: HTTP 500 (×3)");
    expect(view.severity).toBe("error");
  });

  it("renders legacy bare strings unchanged", () => {
    const view = logLineView("waiting: dependency unfulfilled");
    expect(view).toEqual({
      timestamp: null,
      text: "waiting: dependency unfulfilled",
      severity: null,
    });
  });

  it("still renders the previous task.log record shape", () => {
    const view = logLineView({ type: "task.log", message: "succeeded" });
    expect(view).toEqual({ timestamp: null, text: "succeeded", severity: null });
  });

  it("falls back to JSON for unrecognized records", () => {
    const view = logLineView({ type: "mystery", value: 1 });
    expect(view).toEqual({
      timestamp: null,
      text: '{"type":"mystery","value":1}',
      severity: null,
    });
  });
});

describe("dedupLogLines", () => {
  it("collapses consecutive identical lines into one with a count", () => {
    const lines = dedupLogLines([
      "fetch A",
      "fetch A",
      "fetch A",
      "done",
    ]);
    expect(lines).toEqual([
      { view: { timestamp: null, text: "fetch A", severity: null }, count: 3 },
      { view: { timestamp: null, text: "done", severity: null }, count: 1 },
    ]);
  });

  it("keeps non-consecutive repeats separate", () => {
    const lines = dedupLogLines(["a", "b", "a"]);
    expect(lines.map((l) => [l.view.text, l.count])).toEqual([
      ["a", 1],
      ["b", 1],
      ["a", 1],
    ]);
  });

  it("keeps the first occurrence's timestamp", () => {
    const lines = dedupLogLines([
      { type: "log", time: EPOCH, message: "same" },
      { type: "log", time: EPOCH + 5, message: "same" },
    ]);
    expect(lines).toHaveLength(1);
    expect(lines[0].count).toBe(2);
    expect(lines[0].view.timestamp).toBe(expectedClock(EPOCH));
  });

  it("does not merge lines that differ only in severity", () => {
    const lines = dedupLogLines([
      { type: "task.diagnostic", severity: "warning", code: "c", message: "m", context: {}, count: 1 },
      { type: "task.diagnostic", severity: "error", code: "c", message: "m", context: {}, count: 1 },
    ]);
    expect(lines).toHaveLength(2);
  });

  it("returns an empty list for no entries", () => {
    expect(dedupLogLines([])).toEqual([]);
  });
});
