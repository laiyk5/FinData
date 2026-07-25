/**
 * Self-explanatory readiness labels. The three server-reported readiness
 * facts never share a word and never appear as bare "ready"; a blocked update
 * names why, derived only from server-reported facts.
 */

export interface ReadinessFacts {
  /** Dataset lifecycle state ("ready" means the database has committed data). */
  state: string;
  providerReady: boolean;
  updateReady: boolean;
  /** Keys of unconfigured required settings. */
  missingRequired: string[];
}

/** Provider readiness means the provider's configuration is complete. */
export function providerConfigLabel(ready: boolean): string {
  return ready ? "configured" : "needs configuration";
}

export function updateReadinessLabel(ready: boolean): string {
  return ready ? "update ready to run" : "update blocked";
}

/**
 * Why an update is blocked, in a sentence (used for tooltips and the disabled
 * primary action). Null when the update can run.
 */
export function updateBlockedReason(facts: ReadinessFacts): string | null {
  if (facts.updateReady) return null;
  if (facts.state !== "ready") return `dataset state is ${facts.state}`;
  if (!facts.providerReady) return "provider needs configuration";
  if (facts.missingRequired.length > 0) {
    return `required settings not configured: ${facts.missingRequired.join(", ")}`;
  }
  return "update is not ready";
}

/** Short parenthetical for the dot status line; null when nothing concise applies. */
export function updateBlockedShort(facts: ReadinessFacts): string | null {
  if (facts.updateReady) return null;
  if (!facts.providerReady) return "provider needs configuration";
  if (facts.missingRequired.length > 0) {
    const n = facts.missingRequired.length;
    return `${n} required setting${n === 1 ? "" : "s"} not configured`;
  }
  if (facts.state !== "ready") return `state: ${facts.state}`;
  return null;
}
