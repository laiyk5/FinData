import { describe, expect, it } from "vitest";
import type { ConfigKey } from "./api";
import { filterConfigKeys, groupConfigKeys } from "./configGroups";

function key(partial: Partial<ConfigKey> & { key: string }): ConfigKey {
  return {
    help: "",
    schema: {},
    configured: false,
    secret: false,
    ...partial,
  };
}

const ITEMS: ConfigKey[] = [
  key({ key: "display.timezone", help: "Display timezone for human output" }),
  key({ key: "provider.beta.token", secret: true }),
  key({ key: "provider.alpha.rate_limit", help: "Requests per minute" }),
  key({ key: "dataset.fx.symbols" }),
  key({ key: "provider.alpha.timeout" }),
];

describe("groupConfigKeys", () => {
  it("groups Core first, then providers and datasets alphabetically", () => {
    const groups = groupConfigKeys(ITEMS);
    expect(groups.map((g) => [g.kind, g.name])).toEqual([
      ["core", null],
      ["provider", "alpha"],
      ["provider", "beta"],
      ["dataset", "fx"],
    ]);
  });

  it("sorts keys within a group and labels the group", () => {
    const groups = groupConfigKeys(ITEMS);
    const alpha = groups.find((g) => g.name === "alpha");
    expect(alpha?.label).toBe("provider: alpha");
    expect(alpha?.keys.map((k) => k.key)).toEqual([
      "provider.alpha.rate_limit",
      "provider.alpha.timeout",
    ]);
  });

  it("omits empty groups", () => {
    const groups = groupConfigKeys([key({ key: "display.timezone" })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].kind).toBe("core");
  });
});

describe("filterConfigKeys", () => {
  it("matches key and help text case-insensitively", () => {
    expect(filterConfigKeys(ITEMS, "TIMEZONE").map((k) => k.key)).toEqual([
      "display.timezone",
    ]);
    expect(filterConfigKeys(ITEMS, "requests per").map((k) => k.key)).toEqual([
      "provider.alpha.rate_limit",
    ]);
    expect(filterConfigKeys(ITEMS, "provider.alpha")).toHaveLength(2);
  });

  it("returns everything for a blank query and nothing for a miss", () => {
    expect(filterConfigKeys(ITEMS, "  ")).toHaveLength(ITEMS.length);
    expect(filterConfigKeys(ITEMS, "zzz")).toHaveLength(0);
  });
});
