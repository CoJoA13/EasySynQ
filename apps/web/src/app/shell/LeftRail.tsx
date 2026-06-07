import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import { useClauses } from "./useClauses";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

export function LeftRail() {
  const { pathname } = useLocation();
  const { data: clauses } = useClauses();
  return (
    <Stack gap="xs" p="sm">
      <NavLink component={Link} to="/" label="Home" active={pathname === "/"} />
      <NavLink
        component={Link}
        to="/library"
        label="Library"
        active={pathname.startsWith("/library")}
      />
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
