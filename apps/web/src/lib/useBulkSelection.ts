// Reusable bulk-selection primitive (critique #5) — the DOC_ACK acknowledge-inbox pattern, extracted
// so a homogeneous task inbox can offer "select N → one action" without re-rolling the Set bookkeeping
// each time. `selectable` (optional) bounds "select all" to eligible rows — the ingestion
// `included_candidate` guard, so a non-selectable row can never be swept into a bulk action.
// NOTE (owner decision): bulk stays acknowledge-only (no signature) — there is deliberately NO
// bulk-approve, since each approval writes a signed, SoD-gated decision.

import { useCallback, useMemo, useState } from "react";

export function useBulkSelection<T extends { id: string }>(
  rows: readonly T[],
  selectable?: (row: T) => boolean,
): {
  selected: Set<string>;
  toggle: (id: string) => void;
  toggleAll: () => void;
  clear: () => void;
  allSelected: boolean;
  count: number;
  selectedIds: string[];
} {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const eligible = useMemo(
    () => rows.filter((r) => (selectable ? selectable(r) : true)),
    [rows, selectable],
  );
  const selectedIds = useMemo(
    () => eligible.filter((r) => selected.has(r.id)).map((r) => r.id),
    [eligible, selected],
  );
  const allSelected = eligible.length > 0 && selectedIds.length === eligible.length;
  const toggle = useCallback((id: string) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }, []);
  const toggleAll = useCallback(() => {
    setSelected(allSelected ? new Set() : new Set(eligible.map((r) => r.id)));
  }, [allSelected, eligible]);
  const clear = useCallback(() => setSelected(new Set()), []);
  return {
    selected,
    toggle,
    toggleAll,
    clear,
    allSelected,
    count: selectedIds.length,
    selectedIds,
  };
}
