/**
 * Contextual event actions, derived generically from an event's `kind` and
 * `context` — never from specific dataset or provider names. Task-failure and
 * queue events link to the filtered task list, cron events to the filtered
 * cron list, and any event naming a dataset links to its detail.
 */

export interface EventAction {
  label: string;
  to: string;
}

const TASK_KINDS: ReadonlySet<string> = new Set([
  "task_failed",
  "queue_rejected",
  "liveness_timeout",
]);

const CRON_KINDS: ReadonlySet<string> = new Set([
  "cron_missed",
  "cron_skipped",
  "cron_dst_gap",
]);

export function eventActions(event: {
  kind: string;
  context: Record<string, unknown>;
}): EventAction[] {
  const dataset =
    typeof event.context?.dataset === "string" ? event.context.dataset : null;
  if (dataset === null) return [];
  const enc = encodeURIComponent(dataset);
  const actions: EventAction[] = [];
  if (TASK_KINDS.has(event.kind)) {
    actions.push({ label: "View tasks", to: `/tasks?dataset=${enc}` });
  }
  if (CRON_KINDS.has(event.kind)) {
    actions.push({ label: "View cron job", to: `/cron?dataset=${enc}` });
  }
  actions.push({ label: "Open dataset", to: `/datasets/${enc}` });
  return actions;
}
