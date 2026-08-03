import { Box, Button, Stack, Text } from "@mantine/core";
import { useClauses } from "../../app/shell/useClauses";
import type { Clause, PdcaPhase } from "../../lib/types";

const PHASES: { phase: PdcaPhase; label: string }[] = [
  { phase: "PLAN", label: "Plan" },
  { phase: "DO", label: "Do" },
  { phase: "CHECK", label: "Check" },
  { phase: "ACT", label: "Act" },
];

// The in-page clause-spine filter (PDCA-banded). Clicking a clause sets the exact-number Clause
// filter (clicking the active one clears it). Top-level clauses always render; only the SELECTED
// top-level clause exposes its direct sub-clauses (collapse-to-selection keeps the spine ~12 rows
// instead of ~40, and a deep link to a sub-clause keeps its parent subtree open). Documents map to
// specific (often sub-)clauses and the GET /documents clause filter is an EXACT number match (no
// subtree rollup yet), so the sub-clauses must stay pickable. Per-clause doc counts are deferred
// (an authz-correct count is an aggregation — see the S-web-2 spec §9). Filter buttons, not nav
// links — these refine the list, they don't navigate.
export function ClauseTree({
  selected,
  onSelect,
}: {
  selected?: string;
  onSelect: (clauseNumber: string | undefined) => void;
}) {
  const { data: clauses } = useClauses();
  const all = clauses ?? [];
  const selectedTop = selected?.split(".")[0];

  return (
    <Stack gap="xs" aria-label="Clause spine filter">
      {PHASES.map(({ phase, label }) => {
        const top = all.filter((c) => c.pdca_phase === phase && c.parent_id === null);
        if (top.length === 0) return null;
        const items: { clause: Clause; indent: boolean }[] = [];
        for (const c of top) {
          items.push({ clause: c, indent: false });
          if (c.number === selectedTop) {
            for (const ch of all.filter((k) => k.parent_id === c.id)) {
              items.push({ clause: ch, indent: true });
            }
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
                styles={{
                  // compact-sm is a FIXED height while the label wraps — let the row grow instead
                  // of overflowing into its neighbor (owner: never truncate clause titles).
                  root: {
                    fontWeight: 400,
                    height: "auto",
                    minHeight: "var(--button-height-compact-sm)",
                    paddingBlock: 4,
                  },
                  label: {
                    whiteSpace: "normal",
                    textAlign: "left",
                    lineHeight: 1.35,
                    display: "flex",
                    alignItems: "baseline",
                    columnGap: 6,
                  },
                }}
                pl={indent ? "lg" : undefined}
              >
                {/* Separate flex spans give a true hanging indent: wrapped title lines align under
                    the title, not the clause number. The literal space keeps the accessible name
                    one string ("8.4 Control of …"). */}
                <span style={{ flexShrink: 0 }}>{clause.number}</span>{" "}
                <span>
                  {clause.title}
                  {clause.is_mandatory_star ? " ★" : ""}
                </span>
              </Button>
            ))}
          </Box>
        );
      })}
    </Stack>
  );
}
