import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import { usePermissions } from "./usePermissions";
import { useClauses } from "./useClauses";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

export function LeftRail() {
  const { pathname } = useLocation();
  const { data: clauses } = useClauses();
  const { can } = usePermissions();
  return (
    <Stack gap="xs" p="sm">
      <NavLink component={Link} to="/" label="Home" active={pathname === "/"} />
      <NavLink
        component={Link}
        to="/library"
        label="Library"
        active={pathname.startsWith("/library")}
      />
      <NavLink
        component={Link}
        to="/tasks"
        label="Review & Approve"
        active={pathname.startsWith("/tasks")}
      />
      {can("report.compliance_checklist.read") && (
        // S-web-6: gated — only QMS Owner / Internal Auditor hold the SYSTEM report key.
        <NavLink
          component={Link}
          to="/compliance"
          label="Compliance"
          active={pathname.startsWith("/compliance")}
        />
      )}
      <NavLink
        component={Link}
        to="/capa"
        label="Nonconformity & CAPA"
        active={pathname.startsWith("/capa")}
      />
      <NavLink
        component={Link}
        to="/audits"
        label="Internal Audit"
        active={pathname.startsWith("/audits")}
      />
      {can("changeRequest.read") && (
        // S-dcr-ui-1: gated — changeRequest.read; the change-control (DCR) register.
        <NavLink
          component={Link}
          to="/dcrs"
          label="Change requests"
          active={pathname.startsWith("/dcrs")}
        />
      )}
      {can("import.review") && (
        // S-ing-4b: gated — import review is an admin-only SYSTEM key (no ABAC scope).
        <NavLink
          component={Link}
          to="/ingestion"
          label="Import"
          active={pathname.startsWith("/ingestion")}
        />
      )}
      {can("drift.read") && (
        // S-web-8: gated — drift.read is the admin-side SYSTEM key (R41); System Administrator
        // holds it natively (seeded 0047).
        <NavLink
          component={Link}
          to="/drift"
          label="Drift"
          active={pathname.startsWith("/drift")}
        />
      )}
      {can("objective.read") && (
        // S-obj-2: gated — objective.read (PROCESS finest-scope, SYSTEM fallback in v1); the PLAN-phase
        // register (clause 6.2). Mirrors the drift.read entry.
        <NavLink
          component={Link}
          to="/objectives"
          label="Objectives"
          active={pathname.startsWith("/objectives")}
        />
      )}
      {can("mgmtReview.read") && (
        // S-mr-2: gated — mgmtReview.read (SYSTEM finest-scope); the CHECK-phase clause-9.3 register.
        <NavLink
          component={Link}
          to="/management-reviews"
          label="Management reviews"
          active={pathname.startsWith("/management-reviews")}
        />
      )}
      {PHASES.map((phase) => {
        const top = (clauses ?? []).filter((c) => c.pdca_phase === phase && c.parent_id === null);
        if (top.length === 0) return null;
        return (
          <Box key={phase} mt="sm">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs">
              {phase}
            </Text>
            {top.map((c) => (
              // S-web-2: a clause link filters the Library by that exact clause number.
              <NavLink
                key={c.id}
                component={Link}
                to={`/library?clause=${encodeURIComponent(c.number)}`}
                label={`${c.number} ${c.title}`}
              />
            ))}
          </Box>
        );
      })}
    </Stack>
  );
}
