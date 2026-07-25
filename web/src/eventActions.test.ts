import { describe, expect, it } from "vitest";
import { eventActions } from "./eventActions";

describe("eventActions", () => {
  it("links task-failure kinds to the filtered task list and the dataset", () => {
    for (const kind of ["task_failed", "queue_rejected", "liveness_timeout"]) {
      expect(eventActions({ kind, context: { dataset: "daily" } })).toEqual([
        { label: "View tasks", to: "/tasks?dataset=daily" },
        { label: "Open dataset", to: "/datasets/daily" },
      ]);
    }
  });

  it("links cron kinds to the filtered cron list and the dataset", () => {
    for (const kind of ["cron_missed", "cron_skipped", "cron_dst_gap"]) {
      expect(eventActions({ kind, context: { dataset: "daily" } })).toEqual([
        { label: "View cron job", to: "/cron?dataset=daily" },
        { label: "Open dataset", to: "/datasets/daily" },
      ]);
    }
  });

  it("links any dataset-bearing event to the dataset detail", () => {
    expect(eventActions({ kind: "something_else", context: { dataset: "fx" } })).toEqual([
      { label: "Open dataset", to: "/datasets/fx" },
    ]);
  });

  it("offers nothing when no dataset is named", () => {
    expect(eventActions({ kind: "task_failed", context: {} })).toEqual([]);
    expect(eventActions({ kind: "task_failed", context: { dataset: 42 } })).toEqual([]);
  });

  it("encodes dataset names in the links", () => {
    const actions = eventActions({ kind: "cron_missed", context: { dataset: "a b" } });
    expect(actions[0].to).toBe("/cron?dataset=a%20b");
  });
});
