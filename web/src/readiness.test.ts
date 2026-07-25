import { describe, expect, it } from "vitest";
import {
  providerConfigLabel,
  updateBlockedReason,
  updateBlockedShort,
  updateReadinessLabel,
} from "./readiness";

describe("readiness labels", () => {
  it("labels provider readiness as configuration", () => {
    expect(providerConfigLabel(true)).toBe("configured");
    expect(providerConfigLabel(false)).toBe("needs configuration");
  });

  it("labels update readiness as runnable or blocked", () => {
    expect(updateReadinessLabel(true)).toBe("update ready to run");
    expect(updateReadinessLabel(false)).toBe("update blocked");
  });
});

describe("updateBlockedReason", () => {
  const base = {
    state: "ready",
    providerReady: true,
    updateReady: false,
    missingRequired: [] as string[],
  };

  it("is null when the update can run", () => {
    expect(updateBlockedReason({ ...base, updateReady: true })).toBeNull();
  });

  it("names the dataset state when there is no committed data", () => {
    expect(updateBlockedReason({ ...base, state: "uninitialized" })).toBe(
      "dataset state is uninitialized",
    );
  });

  it("names provider configuration next", () => {
    expect(updateBlockedReason({ ...base, providerReady: false })).toBe(
      "provider needs configuration",
    );
  });

  it("names unconfigured required settings", () => {
    expect(
      updateBlockedReason({ ...base, missingRequired: ["dataset.x.symbols"] }),
    ).toBe("required settings not configured: dataset.x.symbols");
  });

  it("falls back to the bare server fact when no cause is known", () => {
    expect(updateBlockedReason(base)).toBe("update is not ready");
  });
});

describe("updateBlockedShort", () => {
  const base = {
    state: "ready",
    providerReady: true,
    updateReady: false,
    missingRequired: [] as string[],
  };

  it("is null when runnable", () => {
    expect(updateBlockedShort({ ...base, updateReady: true })).toBeNull();
  });

  it("prefers provider configuration, then settings, then state", () => {
    expect(updateBlockedShort({ ...base, providerReady: false })).toBe(
      "provider needs configuration",
    );
    expect(
      updateBlockedShort({ ...base, missingRequired: ["a", "b"] }),
    ).toBe("2 required settings not configured");
    expect(updateBlockedShort({ ...base, state: "uninitialized" })).toBe(
      "state: uninitialized",
    );
    expect(updateBlockedShort(base)).toBeNull();
  });
});
