import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation, useSearchParams } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  type SortDir,
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "./registerControls";

function router(initialEntries: string[] = ["/"]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>;
  };
}

describe("useUrlParam", () => {
  it("seeds from the URL and round-trips writes through the URL", () => {
    const { result } = renderHook(() => useUrlParam("state"), {
      wrapper: router(["/?state=Open"]),
    });
    expect(result.current[0]).toBe("Open");
    act(() => result.current[1]("Closed"));
    expect(result.current[0]).toBe("Closed");
    act(() => result.current[1](""));
    expect(result.current[0]).toBe("");
  });
});

describe("useTableSort", () => {
  const KEYS = ["identifier", "due"] as const;

  it("falls back to defaultSort/defaultDir for an absent or unknown URL sort", () => {
    const { result } = renderHook(
      () => useTableSort({ keys: KEYS, defaultSort: "due", defaultDir: "desc" }),
      { wrapper: router(["/?sort=bogus"]) },
    );
    expect(result.current.sort).toBe("due");
    expect(result.current.dir).toBe("desc");
  });

  it("toggles direction on the active column and switches columns at defaultDir", () => {
    const { result } = renderHook(() => useTableSort({ keys: KEYS, defaultDir: "asc" }), {
      wrapper: router(["/"]),
    });
    act(() => result.current.toggleSort("identifier"));
    expect(result.current.sort).toBe("identifier");
    expect(result.current.dir).toBe("asc");
    act(() => result.current.toggleSort("identifier"));
    expect(result.current.dir).toBe("desc");
    act(() => result.current.toggleSort("due"));
    expect(result.current.sort).toBe("due");
    expect(result.current.dir).toBe("asc");
  });

  it("flips the default-active column's direction on the FIRST click (Codex #146 regression)", () => {
    // Opened with the default sort active but NO `sort` url param — a click on that already-
    // highlighted column must TOGGLE direction, not re-write the same default (a silent no-op).
    const { result } = renderHook(
      () => useTableSort({ keys: KEYS, defaultSort: "due", defaultDir: "asc" }),
      { wrapper: router(["/"]) },
    );
    expect(result.current.sort).toBe("due");
    expect(result.current.dir).toBe("asc");
    act(() => result.current.toggleSort("due"));
    expect(result.current.sort).toBe("due");
    expect(result.current.dir).toBe("desc");
  });
});

describe("useDebouncedSearch", () => {
  it("seeds the query from the URL and exposes a trimmed lower-cased term", () => {
    const { result } = renderHook(() => useDebouncedSearch(), {
      wrapper: router(["/?q=Hello"]),
    });
    expect(result.current.q).toBe("Hello");
    expect(result.current.query).toBe("hello");
  });

  it("debounces the typed value into the matchable query", async () => {
    const { result } = renderHook(() => useDebouncedSearch("q", 10), { wrapper: router(["/"]) });
    act(() => result.current.setQ("  Mara  "));
    expect(result.current.q).toBe("  Mara  ");
    await waitFor(() => expect(result.current.query).toBe("mara"));
  });

  it("adopts an external url ?q change and does NOT clobber it (Codex #146 regression)", async () => {
    // An external writer (back/forward, a same-route link, ⌘K) changes ?q while the register stays
    // mounted — simulated here with a sibling that calls setSearchParams.
    function ExternalSetter() {
      const [, setParams] = useSearchParams();
      return <button onClick={() => setParams({ q: "bar" }, { replace: true })}>ext</button>;
    }
    function Harness() {
      const { q, setQ } = useDebouncedSearch("q", 10);
      const loc = useLocation();
      return (
        <>
          <input aria-label="s" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
          <span data-testid="loc">{loc.search}</span>
        </>
      );
    }
    render(
      <MemoryRouter initialEntries={["/?q=foo"]}>
        <Harness />
        <ExternalSetter />
      </MemoryRouter>,
    );
    const input = screen.getByLabelText("s") as HTMLInputElement;
    expect(input.value).toBe("foo");
    fireEvent.click(screen.getByText("ext"));
    // the input adopts the new url value (the sync effect)...
    await waitFor(() => expect(input.value).toBe("bar"));
    // ...and the stale "foo" is never written back over ?q=bar (the debounced-keyed write guard).
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.getByTestId("loc").textContent).toContain("q=bar");
    expect(screen.getByTestId("loc").textContent).not.toContain("q=foo");
  });
});

describe("sortRows", () => {
  type Row = { id: string; n: number | null; s: string };
  const rows: Row[] = [
    { id: "a", n: 3, s: "banana" },
    { id: "b", n: 1, s: "apple" },
    { id: "c", n: null, s: "cherry" },
  ];
  const get = (r: Row, k: "n" | "s") => r[k];

  it("returns a copy unchanged when no column is active", () => {
    const out = sortRows(rows, null, "asc", get);
    expect(out.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(out).not.toBe(rows);
  });

  it("sorts numbers ascending with nulls last", () => {
    const out = sortRows(rows, "n", "asc", get);
    expect(out.map((r) => r.id)).toEqual(["b", "a", "c"]);
  });

  it("keeps nulls last even when descending", () => {
    const out = sortRows(rows, "n", "desc", get);
    expect(out.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("sorts strings locale-aware", () => {
    const dir: SortDir = "asc";
    const out = sortRows(rows, "s", dir, get);
    expect(out.map((r) => r.s)).toEqual(["apple", "banana", "cherry"]);
  });
});
