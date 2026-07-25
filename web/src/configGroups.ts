import type { ConfigKey } from "./api";

/**
 * Config-key classification for the Config page: generic grouping by key
 * prefix (Core / one group per provider / one per dataset) plus text
 * filtering. No key-, provider-, or dataset-specific rules — a newly
 * registered plugin's keys group automatically.
 */

export type ConfigGroupKind = "core" | "provider" | "dataset";

export interface ConfigGroup {
  kind: ConfigGroupKind;
  /** Group title, e.g. "Core" or "provider: tushare". */
  label: string;
  /** Provider/dataset name for provider/dataset groups; null for Core. */
  name: string | null;
  keys: ConfigKey[];
}

export function groupConfigKeys(items: ConfigKey[]): ConfigGroup[] {
  const core: ConfigKey[] = [];
  const providers = new Map<string, ConfigKey[]>();
  const datasets = new Map<string, ConfigKey[]>();
  for (const item of items) {
    if (item.key.startsWith("provider.")) {
      const name = item.key.split(".")[1] ?? "";
      providers.set(name, [...(providers.get(name) ?? []), item]);
    } else if (item.key.startsWith("dataset.")) {
      const name = item.key.split(".")[1] ?? "";
      datasets.set(name, [...(datasets.get(name) ?? []), item]);
    } else {
      core.push(item);
    }
  }
  const sortByKey = (a: ConfigKey, b: ConfigKey): number => a.key.localeCompare(b.key);
  const groups: ConfigGroup[] = [];
  if (core.length > 0) {
    groups.push({ kind: "core", label: "Core", name: null, keys: core.sort(sortByKey) });
  }
  for (const [name, keys] of [...providers.entries()].sort()) {
    groups.push({
      kind: "provider",
      label: `provider: ${name}`,
      name,
      keys: keys.sort(sortByKey),
    });
  }
  for (const [name, keys] of [...datasets.entries()].sort()) {
    groups.push({
      kind: "dataset",
      label: `dataset: ${name}`,
      name,
      keys: keys.sort(sortByKey),
    });
  }
  return groups;
}

/** Case-insensitive text filter over key and help text. */
export function filterConfigKeys(items: ConfigKey[], query: string): ConfigKey[] {
  const q = query.trim().toLowerCase();
  if (q === "") return items;
  return items.filter(
    (item) =>
      item.key.toLowerCase().includes(q) || item.help.toLowerCase().includes(q),
  );
}
