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

// ── Rail destination icons (S-ui-2) ────────────────────────────────────────────────────────────
// One per LeftRail destination, so the 17-item PDCA grouping reads as a scannable list rather than
// a wall of text. Same 24×24 grid, same 1.75 stroke, same currentColor + aria-hidden contract as the
// set above: the NavLink's own label carries the accessible name.
//
// These were tuned by MEASURING them, not by reading the paths. Rasterised at the rail's real 17px
// and summed as alpha-weighted ink, the seventeen span 29.1–80.3 (2.76x, mean 55). The remaining
// spread is inherent to the metaphors — a diverging line (Drift, 29.1) cannot carry the ink of three
// stacked volumes (Library, 80.3) without ceasing to be a diverging line — so it is a bounded and
// deliberate property rather than an unexamined one. If a new glyph lands far outside that band its
// row will read bolder or fainter than its neighbours; re-measure rather than eyeball it.
//
// The same pass caught four defects that reading the source did not: two exclamation dots drawn as
// zero-length lines (visible only because strokeLinecap is "round"), a second figure in IconUsers
// made of detached arcs, arrowheads closer together than one stroke width, and a page outline with
// no right-hand edge.

export function IconHome(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 11.5 12 5l8 6.5" />
      <path d="M6.5 10.3V20h11v-9.7" />
    </Svg>
  );
}

// Objectives — a target (the measurable-aim metaphor, distinct from the CHECK reads).
export function IconTarget(props: IconProps) {
  return (
    <Svg {...props}>
      {/* Two rings, not three: at the rail's 17px the r=1.1 centre filled in as a blob against the
          r=4 ring. The arrow also separates this from IconGlobe, which sits two rows above it in
          the same PLAN group and would otherwise be a second ringed circle. */}
      <circle cx="10.8" cy="13.2" r="7.2" />
      <circle cx="10.8" cy="13.2" r="3" />
      <line x1="13.4" y1="10.6" x2="20.4" y2="3.6" />
      <polyline points="16.2 3.6 20.4 3.6 20.4 7.8" />
    </Svg>
  );
}

// Risk & opportunity — a diamond (the risk-matrix cell), deliberately a DIFFERENT outline from the
// CAPA triangle so the two "something needs attention" rails are not confusable at a glance.
export function IconRisk(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.2 20.8 12 12 20.8 3.2 12z" />
      <line x1="12" y1="8.6" x2="12" y2="12.8" />
      {/* A real short stroke, not a zero-length line: the latter draws a dot only while
          strokeLinecap stays "round", and vanishes silently if that ever changes. */}
      <line x1="12" y1="15.5" x2="12" y2="15.9" />
    </Svg>
  );
}

// Context of the organization — a globe (the external/internal issues of clause 4.1).
export function IconGlobe(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="7.4" />
      <line x1="4.6" y1="12" x2="19.4" y2="12" />
      {/* A deeper bulge: at 2.6 units the meridian flattened to a vertical stroke at rail size and
          the glyph read as a crosshair. */}
      <path d="M12 4c3.8 3.3 3.8 13.4 0 16-3.8-2.6-3.8-12.7 0-16" />
    </Svg>
  );
}

// Interested parties — two figures (clause 4.2).
export function IconUsers(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="9.2" cy="8.4" r="3.4" />
      <path d="M3.6 19.4a5.6 5.6 0 0 1 11.2 0" />
      {/* A COMPLETE second figure. Drawn as a half-arc head it read at rail size as one person
          beside two floating specks. */}
      <circle cx="17.4" cy="7.6" r="2.4" />
      <path d="M16.2 13.2a4.8 4.8 0 0 1 4.2 6.2" />
    </Svg>
  );
}

// Library — shelved volumes. Distinct from IconDocument (a single page), which stays the ingestion
// kind glyph.
export function IconLibrary(props: IconProps) {
  return (
    <Svg {...props}>
      {/* Three volumes, no band. Rasterised at the rail's 17px the banded version carried the
          most ink of any glyph in the set (88.9 vs a 29.1 floor), so its row read bolder than its
          neighbours. */}
      <rect x="4.2" y="5.6" width="3.8" height="12.8" rx="1" />
      <rect x="10.1" y="5.6" width="3.8" height="12.8" rx="1" />
      <rect x="16" y="5.6" width="3.8" height="12.8" rx="1" />
    </Svg>
  );
}

// Import — into the tray. The arrow points INTO the system (ingestion), never out.
export function IconImport(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="12" y1="3.5" x2="12" y2="13" />
      <polyline points="8 9.2 12 13.2 16 9.2" />
      <path d="M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
    </Svg>
  );
}

// Compliance — a shield with a check. IconShield (plain) stays the ingestion/authority glyph.
export function IconCompliance(props: IconProps) {
  return (
    <Svg {...props}>
      {/* A checklist, not a shield. This previously reused IconShield's d attribute verbatim, so
          two unrelated meanings shared one silhouette - and both appear on /imports. It is also
          deliberately not a clipboard, which is IconTasks. */}
      <polyline points="3.5 8 5.6 10.1 9.4 5.8" />
      <line x1="12.4" y1="8" x2="20.5" y2="8" />
      <polyline points="3.5 16.2 5.6 18.3 9.4 14" />
      <line x1="12.4" y1="16.2" x2="20.5" y2="16.2" />
    </Svg>
  );
}

// Internal audit — examining a document. Reuses the magnifier vocabulary of IconSearch but bound to
// a page, so the rail entry is not confusable with the TopBar search control.
export function IconAudit(props: IconProps) {
  return (
    <Svg {...props}>
      {/* The page now closes on the right. Without that edge the outline read as an open bracket
          with a detached step rather than a document. */}
      <path d="M13.4 3H6.6A1.6 1.6 0 0 0 5 4.6v14.8A1.6 1.6 0 0 0 6.6 21H10.2" />
      <path d="M13.4 3 18.6 8.2V11.4" />
      <path d="M13.4 3v5.2h5.2" />
      <circle cx="15.4" cy="15.6" r="3.8" />
      <line x1="18.1" y1="18.3" x2="21" y2="21.2" />
    </Svg>
  );
}

// Management review — the review board with a trend, i.e. inputs read together at a point in time.
export function IconManagementReview(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="4" width="18" height="12.5" rx="2" />
      <line x1="12" y1="16.5" x2="12" y2="20" />
      <polyline points="7.2 12.4 10 9.6 12.4 11.8 16.8 7.6" />
    </Svg>
  );
}

// Drift — one path separating into two, the divergence between the controlled state and reality.
export function IconDrift(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 12h5" />
      <path d="M8 12c4 0 5-5 13-5" />
      <path d="M8 12c4 0 5 5 13 5" />
    </Svg>
  );
}

// Master document list — the tabular read of controlled documents.
export function IconRegister(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <line x1="3" y1="9.6" x2="21" y2="9.6" />
      <line x1="9.2" y1="9.6" x2="9.2" y2="19" />
    </Svg>
  );
}

// Nonconformity and CAPA — a warning triangle. See IconRisk on why these two differ in outline.
export function IconAlertTriangle(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 4.4 21 19.6H3z" />
      <line x1="12" y1="10.2" x2="12" y2="14.2" />
      {/* See IconRisk: a short stroke rather than a zero-length line. */}
      <line x1="12" y1="16.8" x2="12" y2="17.2" />
    </Svg>
  );
}

// Improvement — a rising trend.
export function IconTrendUp(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="3 17 9 11 13 15 21 7" />
      <polyline points="15.4 7 21 7 21 12.6" />
    </Svg>
  );
}

// Change requests — the two-way swap of a controlled change.
export function IconChangeRequest(props: IconProps) {
  return (
    <Svg {...props}>
      {/* The two arrowheads previously spanned y5.6-11.2 and y12.8-18.4: a 1.6-unit gap against a
          1.75 stroke, so they touched and read as one dark cluster. Now 3.6 units apart. */}
      <path d="M4 7.4h11" />
      <polyline points="12.2 4.6 15 7.4 12.2 10.2" />
      <path d="M20 16.6H9" />
      <polyline points="11.8 13.8 9 16.6 11.8 19.4" />
    </Svg>
  );
}

// ── Shell affordances (S-ui-2) ─────────────────────────────────────────────────────────────────
// IconChevronRight is the Breadcrumb separator. IconClose / IconExternal / IconFilter complete the
// shell vocabulary named in the design spec; the register filter bar (S-ui-4) is what consumes the
// last of them. They are tree-shaken when unreferenced and covered automatically by icons.test.tsx.

// Breadcrumb separator — a chevron, not the default "/", so the trail reads as direction.
export function IconChevronRight(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="9.5 6 15.5 12 9.5 18" />
    </Svg>
  );
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="6.2" y1="6.2" x2="17.8" y2="17.8" />
      <line x1="17.8" y1="6.2" x2="6.2" y2="17.8" />
    </Svg>
  );
}

export function IconExternal(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="14 4 20 4 20 10" />
      <line x1="20" y1="4" x2="11.5" y2="12.5" />
      <path d="M18 14.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h3.5" />
    </Svg>
  );
}

export function IconFilter(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 5.5h16l-6.3 7.5V19l-3.4-1.9v-5.1z" />
    </Svg>
  );
}
