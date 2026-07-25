import { describe, expect, it } from "vitest";
import { datasetStateLabel, formatAbsolute, formatBytes, formatRelativeTime, parseServerTime } from "./format";

const NOW = 1_800_000_000; // fixed reference instant

describe("formatRelativeTime", () => {
  it("renders recent past as just now", () => {
    expect(formatRelativeTime(NOW - 3, NOW)).toBe("just now");
  });

  it("renders seconds, minutes, hours, and days ago", () => {
    expect(formatRelativeTime(NOW - 42, NOW)).toBe("42s ago");
    expect(formatRelativeTime(NOW - 3 * 60, NOW)).toBe("3m ago");
    expect(formatRelativeTime(NOW - 5 * 3600, NOW)).toBe("5h ago");
    expect(formatRelativeTime(NOW - 3 * 86400, NOW)).toBe("3d ago");
  });

  it("renders future times as a countdown", () => {
    expect(formatRelativeTime(NOW + 90, NOW)).toBe("in 2m");
    expect(formatRelativeTime(NOW + 2 * 3600, NOW)).toBe("in 2h");
    expect(formatRelativeTime(NOW + 86400, NOW)).toBe("in 1d");
  });
});

describe("formatAbsolute", () => {
  it("renders in the requested timezone", () => {
    // 2027-01-15T08:00:00Z is 16:00 on Jan 15 in Asia/Shanghai (UTC+8).
    const s = formatAbsolute(1_800_000_000, "Asia/Shanghai");
    expect(s).toContain("2027");
    expect(s).toContain("01");
    expect(s).toContain("16:00");
    expect(s).toContain("GMT+8");
  });

  it("renders a different wall time in another timezone", () => {
    const shanghai = formatAbsolute(1_800_000_000, "Asia/Shanghai");
    const utc = formatAbsolute(1_800_000_000, "UTC");
    expect(shanghai).not.toBe(utc);
    expect(utc).toContain("08:00");
  });

  it("falls back to local formatting for an invalid timezone", () => {
    expect(formatAbsolute(1_800_000_000, "Not/AZone")).toMatch(/\d{4}/);
  });
});

describe("parseServerTime", () => {
  it("passes epoch seconds through", () => {
    expect(parseServerTime(NOW)).toBe(NOW);
  });

  it("parses ISO strings", () => {
    expect(parseServerTime("1970-01-01T00:01:00Z")).toBe(60);
  });

  it("returns null for empty or unparseable values", () => {
    expect(parseServerTime(null)).toBeNull();
    expect(parseServerTime(undefined)).toBeNull();
    expect(parseServerTime("")).toBeNull();
    expect(parseServerTime("not a date")).toBeNull();
  });
});

describe("datasetStateLabel", () => {
  it("renders unambiguous labels for the known states", () => {
    expect(datasetStateLabel("ready")).toBe("has data");
    expect(datasetStateLabel("uninitialized")).toBe("no data");
  });

  it("renders unknown states verbatim", () => {
    expect(datasetStateLabel("migrating")).toBe("migrating");
  });
});

describe("formatBytes", () => {
  it("renders bytes, KB, MB, and GB", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(13002342)).toBe("12.4 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("handles invalid input", () => {
    expect(formatBytes(-5)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });
});
