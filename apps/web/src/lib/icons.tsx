import type { ReactNode, SVGProps } from "react";

// Inline SVG icon set (critique #4) — replaces the decorative emoji (TopBar 🔍⌖🔔👤, ingestion 📄🔒🛡)
// that are an explicit PRODUCT.md anti-reference, render as OS-specific pictographs (breaking the
// air-gap "renders identically on a disconnected box" promise), and clash with the disciplined
// geometric glyph set. Stroke-based, 24×24, consuming `currentColor` so each icon inherits its host
// control's colour (the es tokens). aria-hidden by default — the host ActionIcon/Button/Badge carries
// the accessible name (the critique's "keep existing aria-labels").

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 18, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" />
    </Svg>
  );
}

// Tasks — a clipboard with a check (the general task queue affordance).
export function IconTasks(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1" />
      <path d="M9 13l2 2 4-4" />
    </Svg>
  );
}

export function IconBell(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </Svg>
  );
}

export function IconUser(props: IconProps) {
  // Ink spans y 3–20 (head top → shoulder base) so the optical centre (11.5) matches IconTasks —
  // at the old y 4–21 the person read ~1px lower than its TopBar neighbours despite aligned boxes.
  return (
    <Svg {...props}>
      <circle cx="12" cy="7" r="4" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </Svg>
  );
}

// Document — a page with text lines (the ingestion kind glyph).
export function IconDocument(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <line x1="9" y1="13" x2="15" y2="13" />
      <line x1="9" y1="17" x2="15" y2="17" />
    </Svg>
  );
}

// Record — a padlock (the WORM-controlled record kind glyph).
export function IconRecord(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </Svg>
  );
}

export function IconShield(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" />
    </Svg>
  );
}

// Sort affordances for a sortable column header (critique #5). A single chevron for the active
// direction; the stacked double-chevron for an inactive-but-sortable column. Direction-only — these
// are NOT status glyphs (the retired ▲), so they don't collide with the StatusBadge tone set.
export function IconChevronUp(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="6 15 12 9 18 15" />
    </Svg>
  );
}

export function IconChevronDown(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="6 9 12 15 18 9" />
    </Svg>
  );
}

export function IconChevronSort(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="8 9 12 5 16 9" />
      <polyline points="8 15 12 19 16 15" />
    </Svg>
  );
}
