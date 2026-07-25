/**
 * Client-side task-activity timeline: positions tasks on a trailing time
 * window as individual marks, suited to sparse activity where a bucketed
 * histogram is mostly empty bars.
 */

import type { TaskHandle } from "./api";

export interface TimelinePoint {
  /** Position within the window, 0 (oldest) to 1 (now). */
  x: number;
  task: TaskHandle;
}

export function taskTimelinePoints(
  tasks: TaskHandle[],
  nowSeconds: number,
  windowSeconds = 24 * 3600,
): TimelinePoint[] {
  const start = nowSeconds - windowSeconds;
  return tasks
    .filter((task) => task.created_at >= start && task.created_at <= nowSeconds)
    .map((task) => ({
      x: (task.created_at - start) / windowSeconds,
      task,
    }))
    .sort((a, b) => a.x - b.x);
}

/** Tick labels for the window axis: relative offsets from oldest to "now". */
export function timelineTicks(windowSeconds = 24 * 3600, count = 5): { x: number; label: string }[] {
  const step = windowSeconds / (count - 1);
  return Array.from({ length: count }, (_, i) => {
    const remaining = windowSeconds - i * step;
    if (remaining === 0) return { x: i / (count - 1), label: "now" };
    const hours = Math.round(remaining / 3600);
    return { x: i / (count - 1), label: `-${hours}h` };
  });
}
