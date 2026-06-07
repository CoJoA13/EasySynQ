import { Box, Button, Stack, Text } from "@mantine/core";
import { useClauses } from "../../app/shell/useClauses";
import type { Clause, PdcaPhase } from "../../lib/types";

const PHASES: { phase: PdcaPhase; label: string }[] = [
  { phase: "PLAN", label: "Plan" },
  { phase: "DO", label: "Do" },
  { phase: "CHECK", label: "Check" },
  { phase: "ACT", label: "Act" },
];

const labelOf = (c: Clause) => `${c.number} ${c.title}${c.is_mandatory_star ? " ★" : ""}`;

// The in-page clause-spine filter (PDCA-banded). Clicking a clause sets the exact-number Clause
// filter (clicking the active one clears it). Top-level clauses + their direct children render —
// documents map to specific (often sub-)clauses, and the GET /documents clause filter is an EXACT
// number match (no subtree rollup), so the sub-clauses must be pickable. Per-clause doc counts are
// deferred (an authz-correct count is an aggregation — see the S-web-2 spec §9). Filter buttons, not
// nav links — these refine the list, they don't navigate.
export function ClauseTree({
  selected,
  onSelect,
}: {
  selected?: string;
  onSelect: (clauseNumber: string | undefined) => void;
}) {
  const { data: clauses } = useClauses();
  const all = clauses ?? [];

  return (
    <Stack gap="xs" aria-label="Clause spine filter">
      {PHASES.map(({ phase, label }) => {
        const top = all.filter((c) => c.pdca_phase === phase && c.parent_id === null);
        if (top.length === 0) return null;
        const items: { clause: Clause; indent: boolean }[] = [];
        for (const c of top) {
          items.push({ clause: c, indent: false });
          for (const ch of all.filter((k) => k.parent_id === c.id)) {
            items.push({ clause: ch, indent: true });
          }
        }
        return (
          <Box key={phase}>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs">
              {label}
            </Text>
            {items.map(({ clause, indent }) => (
              <Button
                key={clause.id}
                variant={selected === clause.number ? "light" : "subtle"}
                color="var(--es-accent)"
                size="compact-sm"
                fullWidth
                justify="flex-start"
                aria-pressed={selected === clause.number}
                onClick={() => onSelect(selected === clause.number ? undefined : clause.number)}
                styles={{ root: { fontWeight: 400 }, label: { whiteSpace: "normal", textAlign: "left" } }}
                pl={indent ? "lg" : undefined}
              >
                {labelOf(clause)}
              </Button>
            ))}
          </Box>
        );
      })}
    </Stack>
  );
}
