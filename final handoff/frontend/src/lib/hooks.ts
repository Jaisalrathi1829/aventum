// ============================================================
// Data access hooks.
//
// One hook shape for every read: { data, error, loading, refresh }. Screens branch on
// those four and nothing else, so loading, empty, error and stale states are handled the
// same way everywhere instead of being reinvented per panel (§29).
//
// There is no client-side cache and no optimistic update. After any mutation the screen
// re-reads from the backend, because the backend owns business truth (§2) and a UI that
// predicts the next state is a UI that can be wrong about it.
// ============================================================

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type Resource<T> = {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** True only for the first load, so refreshes don't blank the screen. */
  initialLoading: boolean;
  refresh: () => Promise<void>;
};

export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  options: { enabled?: boolean } = {},
): Resource<T> {
  const enabled = options.enabled !== false;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [initialLoading, setInitialLoading] = useState(enabled);

  // Guards against a slow response from a previous incident overwriting the current one.
  const generation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async () => {
    if (!enabled) return;
    const mine = ++generation.current;
    setLoading(true);
    try {
      const result = await fetcherRef.current();
      if (!mounted.current || mine !== generation.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (!mounted.current || mine !== generation.current) return;
      // Keep the last good data on screen alongside the error rather than blanking it:
      // an operator mid-incident should not lose context because one poll failed.
      setError(
        err instanceof ApiError
          ? err
          : new ApiError("UNEXPECTED", "An unexpected error occurred."),
      );
    } finally {
      if (mounted.current && mine === generation.current) {
        setLoading(false);
        setInitialLoading(false);
      }
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setInitialLoading(false);
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  return { data, error, loading, initialLoading, refresh: load };
}

/**
 * Runs a state-changing request and reports its progress.
 *
 * Deliberately does NOT hold a success flag. A mutation's only lasting effect is in the
 * database, so callers refresh and render what came back; a local `approved = true`
 * would be exactly the forbidden authoritative frontend boolean (§2).
 */
export function useMutation<Args extends unknown[], T>(
  fn: (...args: Args) => Promise<T>,
): {
  run: (...args: Args) => Promise<T | null>;
  pending: boolean;
  error: ApiError | null;
  reset: () => void;
} {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const run = useCallback(
    async (...args: Args) => {
      setPending(true);
      setError(null);
      try {
        return await fn(...args);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err
            : new ApiError("UNEXPECTED", "An unexpected error occurred."),
        );
        return null;
      } finally {
        setPending(false);
      }
    },
    [fn],
  );

  return { run, pending, error, reset: () => setError(null) };
}

/**
 * Polls while `active` is true.
 *
 * Used only for genuinely transient backend states — an execution that has happened but
 * not yet been verified. It stops the moment the condition clears, because §34 forbids
 * excessive polling and a dashboard that reloads forever is a dashboard nobody trusts.
 */
export function usePolling(refresh: () => Promise<void>, active: boolean, intervalMs = 4000) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => void refreshRef.current(), intervalMs);
    return () => clearInterval(id);
  }, [active, intervalMs]);
}
