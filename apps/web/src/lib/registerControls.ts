// Reusable register-triage controls (critique #5, power-user "optimize"): URL-backed text search +
// column sort, so the high-traffic registers (Tasks, DCR, MR, Audits, Objectives) are searchable,
// sortable and SHAREABLE — and the filters survive navigation (the Mara "filters reset on nav" fix)
// instead of living in transient component state. The keyboard row-nav lives in ./useRowKeyboardNav,
// the bulk-select primitive in ./useBulkSelection, and the presentational bits in ./RegisterToolbar.

import { useDebouncedValue } from "@mantine/hooks";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

export type SortDir = "asc" | "desc";

/** Read/write a single URL search-param. Writes use `{ replace: true }` so a filter/sort change
 *  doesn't pollute the back-stack — and the value survives navigation (URL-as-state, app-wide). */
export function useUrlParam(key: string, fallback = ""): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const set = useCallback(
    (v: string) => {
      setParams(
        (p) => {
          if (v) p.set(key, v);
          else p.delete(key);
          return p;
        },
        { replace: true },
      );
    },
    [key, setParams],
  );
  return [value, set];
}

/** A debounced, URL-backed text filter. `q` drives the input (immediate); `query` is the debounced,
 *  trimmed, lower-cased term to match against; the debounced value is mirrored to the URL `key`. The
 *  staleness guard (matching only the settled value) mirrors the CommandPalette precedent. */
export function useDebouncedSearch(
  key = "q",
  delay = 150,
): { q: string; setQ: (v: string) => void; query: string } {
  const [urlQ, setUrlQ] = useUrlParam(key);
  const [q, setQ] = useState(urlQ);
  const [debounced] = useDebouncedValue(q, delay);
  // Refs let the write effect read the latest urlQ + setter WITHOUT subscribing to them. This is
  // load-bearing: react-router's setSearchParams is NOT referentially stable (it changes on every
  // location change), so keying the write effect on it would re-fire on an EXTERNAL url change with
  // the now-stale local value and clobber it (Codex #146).
  const urlQRef = useRef(urlQ);
  urlQRef.current = urlQ;
  const setUrlQRef = useRef(setUrlQ);
  setUrlQRef.current = setUrlQ;
  // Adopt an EXTERNAL url change (back/forward, a same-route `?q=` link, ⌘K nav) into the input —
  // otherwise the input stays on its mount-time value while the url moves on.
  useEffect(() => {
    setQ((cur) => (cur === urlQ ? cur : urlQ));
  }, [urlQ]);
  // Mirror a SETTLED user edit to the url. Keyed on `debounced` ONLY — it changes only when the
  // user's input settles, so an external url change (debounced unchanged) never triggers a write.
  useEffect(() => {
    if (debounced !== urlQRef.current) setUrlQRef.current(debounced);
  }, [debounced]);
  return { q, setQ, query: debounced.trim().toLowerCase() };
}

/** URL-backed column sort. `toggleSort(key)` flips direction on the active column or switches to a
 *  new column at `defaultDir`. An unknown URL `sort` value falls back to `defaultSort` (forward-
 *  compat — a renamed/removed column key never throws). */
export function useTableSort<K extends string>(opts: {
  keys: readonly K[];
  defaultSort?: K | null;
  defaultDir?: SortDir;
}): { sort: K | null; dir: SortDir; toggleSort: (k: K) => void } {
  const [params, setParams] = useSearchParams();
  const keys = opts.keys;
  const defaultSort: K | null = opts.defaultSort ?? null;
  const defaultDir: SortDir = opts.defaultDir ?? "asc";
  const rawSort = params.get("sort");
  const sort: K | null =
    rawSort && (keys as readonly string[]).includes(rawSort) ? (rawSort as K) : defaultSort;
  const rawDir = params.get("dir");
  const dir: SortDir = rawDir === "asc" || rawDir === "desc" ? rawDir : defaultDir;
  const toggleSort = useCallback(
    (k: K) => {
      setParams(
        (p) => {
          // Compare against the EFFECTIVE active sort/dir (incl. the defaults) so the FIRST click on
          // the default-active column toggles its direction rather than re-writing the same default
          // (Codex #146 — an absent `sort` param still renders that column as active/highlighted).
          const cs = p.get("sort");
          const curSort = cs && (keys as readonly string[]).includes(cs) ? cs : defaultSort;
          const cd = p.get("dir");
          const curDir: SortDir = cd === "asc" || cd === "desc" ? cd : defaultDir;
          if (curSort === k) {
            p.set("dir", curDir === "asc" ? "desc" : "asc");
          } else {
            p.set("sort", k);
            p.set("dir", defaultDir);
          }
          return p;
        },
        { replace: true },
      );
    },
    [setParams, keys, defaultSort, defaultDir],
  );
  return { sort, dir, toggleSort };
}

/** Stable, null-safe comparator for client-side sorting. Returns a copy unchanged when no column is
 *  active. Null/undefined values always sort LAST regardless of direction (so an unmeasured/empty
 *  cell never jumps to the top on a desc sort). String compare is locale-aware. */
export function sortRows<T, K extends string>(
  rows: readonly T[],
  sort: K | null,
  dir: SortDir,
  getValue: (row: T, key: K) => string | number | null | undefined,
): T[] {
  if (!sort) return [...rows];
  const factor = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = getValue(a, sort);
    const bv = getValue(b, sort);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}
