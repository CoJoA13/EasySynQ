import { Group, Paper, Text } from "@mantine/core";
import type { ReactNode } from "react";

// The shared outer shell of a register scorecard band — the rollup strip that sits between a
// register's header and its table. Four bands (risk, context, interested parties, objectives) each
// hand-rolled a byte-identical `<Paper withBorder p="md" radius="md" bg="var(--es-surface-2)">`
// wrapping a space-between `<Group>` of a headline `<Text>` and a `<Group gap="xs">` of chips. Only
// the shell is shared: every band keeps its own client-side rollup arithmetic and its own
// StatusBadge vocabulary, because those are the domain and the thing a reader is actually reading.
//
// This is the one limb of the decisions-register "Accepted duplication" entry that its own revisit
// trigger reaches. The entry's other two members (RegisterLifecyclePanel, PublishRegisterModal) set
// no `radius`, so the S-ui programme's 16px card rule lands on them once through the Mantine theme's
// `components` block. These four pass `radius` EXPLICITLY, and an explicit prop beats a theme
// default — so without this shell the rule would have to be applied four times by hand.
//
// `headline` is the band's left-hand summary sentence, `children` its chips. Both are rendered as
// given: a band that computes "3 of 12 high or critical" is stating an observed count, never a
// verdict, and this shell deliberately supplies no default, no tone and no glyph of its own.
export function ScorecardBandShell({
  headline,
  children,
}: {
  headline: ReactNode;
  children: ReactNode;
}) {
  return (
    <Paper withBorder p="md" radius="xl" bg="var(--es-surface-2)">
      <Group justify="space-between" wrap="wrap">
        <Text>{headline}</Text>
        <Group gap="xs">{children}</Group>
      </Group>
    </Paper>
  );
}
