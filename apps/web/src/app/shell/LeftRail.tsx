import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import { GlyphLegend } from "../../lib/GlyphLegend";
import type { PdcaPhase } from "../../lib/types";
import { usePermissions } from "./usePermissions";
import { useClauses } from "./useClauses";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

// The IA flows the way ISO 9001 flows (design principle 1): the feature nav is grouped by PDCA phase,
// mirroring the Home quadrants + the clause spine, and each phase's clause-filter links nest under the
// same heading (one PLAN/DO/CHECK/ACT label set, no duplication). Phase ↔ clause-range labels match the
// Home QuadrantCard chips.
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
    { to: "/risks", label: "Risk register", prefix: "/risks", gate: "register.read" },
  ],
  DO: [
    { to: "/library", label: "Library", prefix: "/library" },
    { to: "/tasks", label: "Review & Approve", prefix: "/tasks" },
    { to: "/ingestion", label: "Import", prefix: "/ingestion", gate: "import.review" },
  ],
  CHECK: [
    {
      to: "/compliance",
      label: "Compliance",
      prefix: "/compliance",
      gate: "report.compliance_checklist.read",
    },
    { to: "/audits", label: "Internal Audit", prefix: "/audits" },
    {
      to: "/management-reviews",
      label: "Management reviews",
      prefix: "/management-reviews",
      gate: "mgmtReview.read",
    },
    { to: "/drift", label: "Drift", prefix: "/drift", gate: "drift.read" },
  ],
  ACT: [
    { to: "/capa", label: "Nonconformity & CAPA", prefix: "/capa" },
    { to: "/improvement", label: "Improvement", prefix: "/improvement", gate: "improvement.read" },
    { to: "/dcrs", label: "Change requests", prefix: "/dcrs", gate: "changeRequest.read" },
  ],
};

export function LeftRail() {
  const { pathname } = useLocation();
  const { data: clauses } = useClauses();
  const { can } = usePermissions();
  return (
    <Stack gap="xs" p="sm">
      <NavLink component={Link} to="/" label="Home" active={pathname === "/"} />

      {PHASES.map((phase) => {
        const items = NAV[phase].filter((it) => !it.gate || can(it.gate));
        const topClauses = (clauses ?? []).filter(
          (c) => c.pdca_phase === phase && c.parent_id === null,
        );
        // Drop a phase entirely when the caller can see neither a feature link nor a clause under it.
        if (items.length === 0 && topClauses.length === 0) return null;
        return (
          <Box key={phase} mt="sm" role="group" aria-label={`${phase} section`}>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs">
              {phase} · {PHASE_CLAUSES[phase]}
            </Text>
            {items.map((it) => (
              <NavLink
                key={it.to}
                component={Link}
                to={it.to}
                label={it.label}
                active={pathname.startsWith(it.prefix)}
              />
            ))}
            {topClauses.length > 0 && (
              <>
                <Text size="0.625rem" fw={600} c="dimmed" tt="uppercase" px="xs" mt={6}>
                  Clauses
                </Text>
                {topClauses.map((c) => (
                  // S-web-2: a clause link filters the Library by that exact clause number.
                  <NavLink
                    key={c.id}
                    component={Link}
                    to={`/library?clause=${encodeURIComponent(c.number)}`}
                    label={`${c.number} ${c.title}`}
                  />
                ))}
              </>
            )}
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
