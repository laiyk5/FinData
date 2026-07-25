import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePoll } from "./hooks";

describe("usePoll", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("calls immediately, then polls on the interval, and stops on unmount", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { unmount } = renderHook(() => usePoll(fn, 1000, true));

    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3000);
    expect(fn).toHaveBeenCalledTimes(4);

    unmount();
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(4);
  });

  it("does not poll while inactive", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    renderHook(() => usePoll(fn, 1000, false));

    vi.advanceTimersByTime(5000);
    expect(fn).not.toHaveBeenCalled();
  });

  it("stops polling when active flips to false", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { rerender } = renderHook(({ active }) => usePoll(fn, 1000, active), {
      initialProps: { active: true },
    });

    vi.advanceTimersByTime(2000);
    expect(fn).toHaveBeenCalledTimes(3);

    rerender({ active: false });
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(3);
  });
});
