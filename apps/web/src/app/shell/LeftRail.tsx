import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import { GlyphLegend } from "../../lib/GlyphLegend";
import type { PdcaPhase } from "../../lib/types";
import { usePermissions } from "./usePermissions";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

// The IA flows the way ISO 9001 flows (design principle 1): the feature nav is grouped by PDCA phase,
// mirroring the Home quadrants + the clause spine (one PLAN/DO/CHECK/ACT label set, no duplication).
// Phase ↔ clause-range labels match the Home QuadrantCard chips. The per-phase clause-filter links
// were removed: at the time each was an exact-match /library?clause=N link on a TOP-LEVEL clause
// (pre-rollup — always zero documents); the Library's in-page ClauseTree is the clause-spine
// surface, and the rail deliberately stays off the per-page /clauses fetch.
const PHASE_CLAUSES: Record<PdcaPhase, string> = {
  PLAN: "Cl 4–6",
  DO: "Cl 7–8",
  CHECK: "Cl 9",
  ACT: "Cl 10",
};

// One feature nav entry. `gate` (a permission key) hides the entry when the caller lacks it (calm-403
// still lives on the page for the unconditional entries — the CAPA precedent); `prefix` drives the
// active state. Owner-confirmed phase placement: Change requests (DCR) sits under ACT with CAPA +
// Improvement (change-as-improvement); Library / Review & Approve / Import are the DO doc-control
// cluster; Objectives is the lone built PLAN register; the CHECK reads are Compliance / Audit / MR /
// Drift.
type NavItem = { to: string; label: string; prefix: string; gate?: string };

const NAV: Record<PdcaPhase, NavItem[]> = {
  PLAN: [
    { to: "/objectives", label: "Objectives", prefix: "/objectives", gate: "objective.read" },
    // Ungated (the CAPA/Library precedent): GET /risks is filter-not-403, so a bound Process-Owner who
    // holds register.read only at PROCESS scope must still see the link (the SYSTEM-scoped `can()` here
    // can't see their grant); a no-grant caller lands on a calm empty register (Codex P2).
    { to: "/risks", label: "Risk & opportunity register", prefix: "/risks" },
    // Ungated, same reasoning: GET /context is filter-not-403 (a no-grant caller → calm empty register).
    { to: "/context", label: "Context", prefix: "/context" },
    // Ungated, same reasoning: GET /interested-parties is filter-not-403 (clause 4.2 register).
    {
      to: "/interested-parties",
      label: "Interested parties",
      prefix: "/interested-parties",
    },
  ],
  DO: [
    { to: "/library", label: "Library", prefix: "/library" },
    // Ungated: record.read can be authorized at PROCESS scope, while this shell query is SYSTEM-only.
    // The row-filtered Records API is the authority; no-grant callers receive the calm empty register.
    { to: "/records", label: "Records", prefix: "/records" },
    { to: "/tasks", label: "Review and approve", prefix: "/tasks" },
    { to: "/imports", label: "Import", prefix: "/imports", gate: "import.review" },
  ],
  CHECK: [
    {
      to: "/compliance",
      label: "Compliance",
      prefix: "/compliance",
      gate: "report.compliance_checklist.read",
    },
    { to: "/audits", label: "Internal audit", prefix: "/audits" },
    {
      to: "/management-reviews",
      label: "Management reviews",
      prefix: "/management-reviews",
      gate: "mgmtReview.read",
    },
    { to: "/drift", label: "Drift", prefix: "/drift", gate: "drift.read" },
    // Ungated (the Risk/Context precedent): GET /reports/document-control admits a PROCESS-scoped
    // report.read holder (the Process Owner) too — the SYSTEM-scoped `can()` here can't see that
    // grant, so gating on it hid the link from a Process Owner who could otherwise open the page by
    // URL (Codex P2). GET is filter-not-403 for document.read but the SURFACE gate itself can still
    // 403 a no-grant caller — the page's own calm NoAccessState handles that.
    {
      to: "/reports/document-control",
      label: "Document register",
      prefix: "/reports/document-control",
    },
  ],
  ACT: [
    { to: "/capa", label: "Nonconformity and CAPA", prefix: "/capa" },
    { to: "/improvement", label: "Improvement", prefix: "/improvement", gate: "improvement.read" },
    { to: "/dcrs", label: "Change requests", prefix: "/dcrs", gate: "changeRequest.read" },
  ],
};

// One compact row height for every rail entry (Mantine NavLink's default block padding reads
// airy at 256px width; 5px keeps the full PDCA grouping on a laptop viewport without a scroll).
const railLink = { root: { paddingBlock: 5 } } as const;

export function LeftRail() {
  const { pathname } = useLocation();
  const { can } = usePermissions();
  return (
    <Stack gap={4} p="sm">
      <NavLink styles={railLink} component={Link} to="/" label="Home" active={pathname === "/"} />

      {PHASES.map((phase) => {
        const items = NAV[phase].filter((it) => !it.gate || can(it.gate));
        // Drop a phase entirely when the caller can see no feature link under it.
        if (items.length === 0) return null;
        return (
          <Box key={phase} mt={8} role="group" aria-label={`${phase} section`}>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs" mb={2}>
              {phase} · {PHASE_CLAUSES[phase]}
            </Text>
            {items.map((it) => (
              <NavLink
                styles={railLink}
                key={it.to}
                component={Link}
                to={it.to}
                label={it.label}
                active={pathname.startsWith(it.prefix)}
              />
            ))}
          </Box>
        );
      })}

      {/* The in-product legend for the canonical non-colour status vocabulary (✓◔✕●○★). */}
      <Box mt="md" px="xs">
        <GlyphLegend />
      </Box>
    </Stack>
  );
}
