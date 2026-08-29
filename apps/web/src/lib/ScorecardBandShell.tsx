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
// trigger reaches, and the reason is the `radius` prop below. These four bands each passed `radius`
// EXPLICITLY, and an explicit prop beats a Mantine theme default — so any future change to the card
// radius would have to be made four times by hand. The entry's other two members
// (RegisterLifecyclePanel, PublishRegisterModal) set no `radius` at all, so they simply follow
// `theme.defaultRadius` and the same change would cost them nothing. That asymmetry, not the line
// count, is why the band shares and the console does not.
//
// The value stays `md`. The programme's §2.4 "16px card radius" rule is NOT shipped: the theme sets
// `defaultRadius: "md"` (8px) and its `components` block carries only Modal and Drawer entries, and
// S-ui-3 gave the Home quadrant cards `radius="lg"` (12px). Moving this band alone to 16px would
// have made it the only 16px surface in the application, sitting directly above an 8px lifecycle
// card on all three register pages. Extracting the shell is what makes that rule a one-line change
// whenever it is settled app-wide; settling it is not this slice's business.
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
    <Paper withBorder p="md" radius="md" bg="var(--es-surface-2)">
      <Group justify="space-between" wrap="wrap">
        <Text>{headline}</Text>
        <Group gap="xs">{children}</Group>
      </Group>
    </Paper>
  );
}
