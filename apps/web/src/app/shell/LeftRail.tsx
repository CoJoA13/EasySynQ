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
      {can("import.review") && (
        // S-ing-4b: gated — import review is an admin-only SYSTEM key (no ABAC scope).
        <NavLink
          component={Link}
          to="/ingestion"
          label="Import"
          active={pathname.startsWith("/ingestion")}
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
