import { describe, expect, it } from "vitest";
import { taskTimelinePoints, timelineTicks } from "./taskTimeline";
import type { TaskHandle } from "./api";

function task(created_at: number, handle_id = "h"): TaskHandle {
  return {
    handle_id,
    execution_id: "e",
    dataset: "ds",
    operation: "update",
    owner: "api",
    status: "succeeded",
    created_at,
    updated_at: created_at,
  };
}

const NOW = 1_800_000_000;

describe("taskTimelinePoints", () => {
  it("maps window edges to x=0 and x=1", () => {
    const points = taskTimelinePoints([task(NOW - 86400, "a"), task(NOW, "b")], NOW);
    expect(points.map((p) => p.x)).toEqual([0, 1]);
  });

  it("drops tasks outside the window", () => {
    const points = taskTimelinePoints(
      [task(NOW - 86401, "old"), task(NOW - 3600, "in"), task(NOW + 60, "future")],
      NOW,
    );
    expect(points.map((p) => p.task.handle_id)).toEqual(["in"]);
  });

  it("sorts oldest first and computes intermediate positions", () => {
    const points = taskTimelinePoints([task(NOW, "b"), task(NOW - 43200, "a")], NOW);
    expect(points.map((p) => p.task.handle_id)).toEqual(["a", "b"]);
    expect(points[0].x).toBeCloseTo(0.5);
  });

  it("returns an empty list for no tasks", () => {
    expect(taskTimelinePoints([], NOW)).toEqual([]);
  });
});

describe("timelineTicks", () => {
  it("spans the window from oldest to now", () => {
    const ticks = timelineTicks(86400, 5);
    expect(ticks.map((t) => t.label)).toEqual(["-24h", "-18h", "-12h", "-6h", "now"]);
    expect(ticks[0].x).toBe(0);
    expect(ticks[4].x).toBe(1);
  });
});
