import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useBulkSelection } from "./useBulkSelection";

type Row = { id: string; ok: boolean };
const rows: Row[] = [
  { id: "a", ok: true },
  { id: "b", ok: true },
  { id: "c", ok: false },
];

describe("useBulkSelection", () => {
  it("toggles individual rows and reports the actionable count", () => {
    const { result } = renderHook(() => useBulkSelection(rows));
    act(() => result.current.toggle("a"));
    expect(result.current.selected.has("a")).toBe(true);
    expect(result.current.count).toBe(1);
    act(() => result.current.toggle("a"));
    expect(result.current.count).toBe(0);
  });

  it("select-all covers every eligible row; clear empties", () => {
    const { result } = renderHook(() => useBulkSelection(rows));
    act(() => result.current.toggleAll());
    expect(result.current.allSelected).toBe(true);
    expect([...result.current.selectedIds].sort()).toEqual(["a", "b", "c"]);
    act(() => result.current.clear());
    expect(result.current.count).toBe(0);
  });

  it("the selectable guard keeps a non-eligible row out of select-all + the actionable set", () => {
    const { result } = renderHook(() => useBulkSelection(rows, (r) => r.ok));
    act(() => result.current.toggleAll());
    expect(result.current.selectedIds).toEqual(["a", "b"]);
    expect(result.current.allSelected).toBe(true);
    // A non-eligible id may sit in the raw Set, but it never reaches the actionable selectedIds.
    act(() => result.current.toggle("c"));
    expect(result.current.selectedIds).toEqual(["a", "b"]);
  });
});
