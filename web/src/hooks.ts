import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Polls `fn` every `intervalMs` while `active` is true. Calls `fn` once
 * immediately when polling (re)starts. Cleans up the interval when the
 * component unmounts or `active` flips to false. The latest `fn` closure is
 * always used without resetting the interval. Rejections are swallowed —
 * pages handle their own error state inside `fn`.
 */
export function usePoll(
  fn: () => void | Promise<void>,
  intervalMs: number,
  active = true,
): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = (): void => {
      if (cancelled) return;
      void Promise.resolve(fnRef.current()).catch(() => undefined);
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, active]);
}

export interface LiveData<T> {
  data: T | null;
  /** Last poll failure, if any — the view should surface a connection warning. */
  error: unknown;
  /** Epoch milliseconds of the last successful poll, for freshness display. */
  lastUpdated: number | null;
  /** True once the first poll (success or failure) has completed. */
  loaded: boolean;
  refresh: () => Promise<void>;
}

/**
 * Polls `loader` on an adaptive interval (the caller picks the cadence from
 * the data, e.g. fast while a task is live) and tracks freshness: the last
 * successful update time and the last error. Poll failures keep the previous
 * data so the view can warn instead of freezing silently.
 */
export function useLiveData<T>(
  loader: () => Promise<T>,
  intervalMs: number,
  active = true,
): LiveData<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const result = await loader();
      setData(result);
      setLastUpdated(Date.now());
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoaded(true);
    }
    // `loader` identity drives re-creation; usePoll always calls the latest.
  }, [loader]);

  usePoll(refresh, intervalMs, active);

  // Refetch immediately when the loader changes (e.g. a filter flipped) —
  // usePoll alone would wait for the next interval tick.
  const firstLoader = useRef(true);
  useEffect(() => {
    if (firstLoader.current) {
      firstLoader.current = false;
      return;
    }
    void refresh();
  }, [refresh]);

  return { data, error, lastUpdated, loaded, refresh };
}

