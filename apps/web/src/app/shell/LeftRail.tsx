import { Badge, Box, Divider, Group, NavLink, Stack, Text } from "@mantine/core";
import type { ComponentType } from "react";
import { Link, useLocation } from "react-router-dom";
import { GlyphLegend } from "../../lib/GlyphLegend";
import {
  IconAlertTriangle,
  IconAudit,
  IconChangeRequest,
  IconCompliance,
  IconDrift,
  IconGlobe,
  IconHome,
  IconImport,
  IconLibrary,
  IconManagementReview,
  IconRecord,
  IconRegister,
  IconRisk,
  IconTarget,
  IconTasks,
  IconTrendUp,
  IconUsers,
} from "../../lib/icons";
import type { PdcaPhase } from "../../lib/types";
import { useMyTasks } from "../../features/home/hooks";
import { resolveTaskCount, taskCountBadge, taskCountLabel } from "./taskCount";
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

// S-ui-2: each phase carries its own hue as a marker, matching the Home quadrant tints. These are
// CATEGORY markers, not status — they never encode a signal, so nothing here is colour-alone.
const PHASE_HUE: Record<PdcaPhase, string> = {
  PLAN: "var(--es-plan)",
  DO: "var(--es-do)",
  CHECK: "var(--es-check)",
  ACT: "var(--es-act)",
};

type IconComponent = ComponentType<{ size?: number }>;

// One feature nav entry. `gate` (a permission key) hides the entry when the caller lacks it (calm-403
// still lives on the page for the unconditional entries — the CAPA precedent); `prefix` drives the
// active state. Owner-confirmed phase placement: Change requests (DCR) sits under ACT with CAPA +
// Improvement (change-as-improvement); Library / Review & Approve / Import are the DO doc-control
// cluster; Objectives is the lone built PLAN register; the CHECK reads are Compliance / Audit / MR /
// Drift.
type NavItem = { to: string; label: string; prefix: string; icon: IconComponent; gate?: string };

const NAV: Record<PdcaPhase, NavItem[]> = {
  PLAN: [
    {
      to: "/objectives",
      label: "Objectives",
      prefix: "/objectives",
      icon: IconTarget,
      gate: "objective.read",
    },
    // Ungated (the CAPA/Library precedent): GET /risks is filter-not-403, so a bound Process-Owner who
    // holds register.read only at PROCESS scope must still see the link (the SYSTEM-scoped `can()` here
    // can't see their grant); a no-grant caller lands on a calm empty register (Codex P2).
    { to: "/risks", label: "Risk & opportunity register", prefix: "/risks", icon: IconRisk },
    // Ungated, same reasoning: GET /context is filter-not-403 (a no-grant caller → calm empty register).
    { to: "/context", label: "Context", prefix: "/context", icon: IconGlobe },
    // Ungated, same reasoning: GET /interested-parties is filter-not-403 (clause 4.2 register).
    {
      to: "/interested-parties",
      label: "Interested parties",
      prefix: "/interested-parties",
      icon: IconUsers,
    },
  ],
  DO: [
    { to: "/library", label: "Library", prefix: "/library", icon: IconLibrary },
    // Ungated: record.read can be authorized at PROCESS scope, while this shell query is SYSTEM-only.
    // The row-filtered Records API is the authority; no-grant callers receive the calm empty register.
    { to: "/records", label: "Records", prefix: "/records", icon: IconRecord },
    { to: "/tasks", label: "Review and approve", prefix: "/tasks", icon: IconTasks },
    {
      to: "/imports",
      label: "Import",
      prefix: "/imports",
      icon: IconImport,
      gate: "import.review",
    },
  ],
  CHECK: [
    {
      to: "/compliance",
      label: "Compliance",
      prefix: "/compliance",
      icon: IconCompliance,
      gate: "report.compliance_checklist.read",
    },
    { to: "/audits", label: "Internal audit", prefix: "/audits", icon: IconAudit },
    {
      to: "/management-reviews",
      label: "Management reviews",
      prefix: "/management-reviews",
      icon: IconManagementReview,
      gate: "mgmtReview.read",
    },
    { to: "/drift", label: "Drift", prefix: "/drift", icon: IconDrift, gate: "drift.read" },
    // Ungated (the Risk/Context precedent): GET /reports/document-control admits a PROCESS-scoped
    // report.read holder (the Process Owner) too — the SYSTEM-scoped `can()` here can't see that
    // grant, so gating on it hid the link from a Process Owner who could otherwise open the page by
    // URL (Codex P2). GET is filter-not-403 for document.read but the SURFACE gate itself can still
    // 403 a no-grant caller — the page's own calm NoAccessState handles that.
    {
      to: "/reports/document-control",
      label: "Document register",
      prefix: "/reports/document-control",
      icon: IconRegister,
    },
  ],
  ACT: [
    { to: "/capa", label: "Nonconformity and CAPA", prefix: "/capa", icon: IconAlertTriangle },
    {
      to: "/improvement",
      label: "Improvement",
      prefix: "/improvement",
      icon: IconTrendUp,
      gate: "improvement.read",
    },
    {
      to: "/dcrs",
      label: "Change requests",
      prefix: "/dcrs",
      icon: IconChangeRequest,
      gate: "changeRequest.read",
    },
  ],
};

// One compact row height for every rail entry (Mantine NavLink's default block padding reads
// airy at 244px width; 5px keeps the full PDCA grouping on a laptop viewport without a scroll).
const railLink = { root: { paddingBlock: 5, borderRadius: "var(--es-radius-md)" } } as const;

// The single nav count the shell can honestly show. Deliberately NOT a badge per destination: there
// is no aggregate counts endpoint, and Home derives its register numbers by fetching each register's
// full list and counting client-side over a CAPPED window — so per-register rail badges would mean
// ~17 list fetches on every route and would still report floors rather than counts. `useMyTasks` is
// self-scoped, already cached under ["my-tasks"] for Home, and needs no permission key.
//
// It follows the house never-a-confident-zero rule (the ack-bell / NotificationBell pattern): a
// number when the count is known and non-zero, an indeterminate marker when the call FAILED, and
// nothing at all on a true zero. A failed count must never render as "0".
/**
 * The /tasks entry, and the single nav count the shell can state honestly.
 *
 * Deliberately NOT a badge per destination: there is no aggregate counts endpoint, and Home derives
 * its register numbers by fetching each register's full list and counting client-side over a CAPPED
 * window — so per-destination rail badges would mean ~17 list fetches on every route and would still
 * report floors rather than counts. `useMyTasks` is self-scoped, needs no permission key, and is
 * already cached under ["my-tasks"] for Home, so the rail shares a query rather than adding one.
 *
 * The decision itself lives in taskCount.ts so every state is unit-testable without a DOM; this
 * component only renders it. The hook is read ONCE and the state derived once, so the badge and the
 * accessible name cannot disagree.
 */
function TaskNavLink({ item, active }: { item: NavItem; active: boolean }) {
  const state = resolveTaskCount(useMyTasks());
  const Icon = item.icon;
  const badge = taskCountBadge(state);
  return (
    <NavLink
      styles={railLink}
      component={Link}
      to={item.to}
      aria-label={taskCountLabel(item.label, state)}
      label={item.label}
      leftSection={<Icon size={17} />}
      rightSection={
        badge === null ? null : (
          <Badge
            size="xs"
            variant={state.kind === "unavailable" ? "default" : "filled"}
            aria-hidden="true"
          >
            {badge}
          </Badge>
        )
      }
      active={active}
    />
  );
}

export function LeftRail() {
  const { pathname } = useLocation();
  const { can } = usePermissions();
  return (
    <Stack gap={4} p="sm">
      <NavLink
        styles={railLink}
        component={Link}
        to="/"
        label="Home"
        leftSection={<IconHome size={17} />}
        active={pathname === "/"}
      />

      {PHASES.map((phase) => {
        const items = NAV[phase].filter((it) => !it.gate || can(it.gate));
        // Drop a phase entirely when the caller can see no feature link under it.
        if (items.length === 0) return null;
        return (
          <Box key={phase} mt={8} role="group" aria-label={`${phase} section`}>
            <Group gap={8} px="xs" mb={2} wrap="nowrap">
              {/* Category marker only — the phase hue never carries a signal, so hiding it from AT
                  loses nothing; the phase name beside it is the accessible content. */}
              <Box
                aria-hidden="true"
                style={{
                  width: 3,
                  height: 12,
                  borderRadius: "var(--es-radius-pill)",
                  background: PHASE_HUE[phase],
                  flexShrink: 0,
                }}
              />
              <Text size="xs" fw={700} c="dimmed" tt="uppercase">
                {phase} · {PHASE_CLAUSES[phase]}
              </Text>
            </Group>
            {items.map((it) => {
              const active = pathname.startsWith(it.prefix);
              if (it.to === "/tasks") {
                return <TaskNavLink key={it.to} item={it} active={active} />;
              }
              const Icon = it.icon;
              return (
                <NavLink
                  styles={railLink}
                  key={it.to}
                  component={Link}
                  to={it.to}
                  label={it.label}
                  leftSection={<Icon size={17} />}
                  active={active}
                />
              );
            })}
          </Box>
        );
      })}

      {/* The in-product legend for the canonical non-colour status vocabulary (✓◔✕●○★), set off
          by a rule so it reads as rail furniture rather than a seventeenth destination. */}
      <Divider mt="md" />
      <Box px="xs">
        <GlyphLegend />
      </Box>
    </Stack>
  );
}
