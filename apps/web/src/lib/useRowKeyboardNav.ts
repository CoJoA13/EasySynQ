// Roving ↑/↓ keyboard row-navigation for the high-traffic registers (critique #5). Attach `ref` to
// the row container (the Mantine `Table.Tbody`) and `onKeyDown` to it; mark each row's PRIMARY
// focusable element (the row's identifier Anchor/link/button) with `data-rownav`. Arrow keys move
// real DOM focus between rows; Enter then activates the focused row natively (no synthetic handler),
// preserving the clean single-Anchor-per-row axe shape the registers already use.

import type { KeyboardEvent, RefObject } from "react";
import { useCallback, useRef } from "react";

export function useRowKeyboardNav<E extends HTMLElement = HTMLTableSectionElement>(): {
  ref: RefObject<E>;
  onKeyDown: (e: KeyboardEvent<E>) => void;
} {
  const ref = useRef<E>(null);
  const onKeyDown = useCallback((e: KeyboardEvent<E>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const container = ref.current;
    if (!container) return;
    const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-rownav]"));
    if (rows.length === 0) return;
    e.preventDefault();
    const active = document.activeElement as HTMLElement | null;
    const idx = active ? rows.indexOf(active) : -1;
    const next =
      e.key === "ArrowDown"
        ? idx < 0
          ? 0
          : Math.min(idx + 1, rows.length - 1)
        : idx <= 0
          ? 0
          : idx - 1;
    rows[next]?.focus();
  }, []);
  return { ref, onKeyDown };
}
