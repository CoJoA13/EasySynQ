import { Box, Group, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { AsOf } from "./AsOf";

// The shared register page header: the title, its optional write affordance, and the freshness
// stamp beneath. Eleven register pages hand-rolled the identical
// `<Group justify="space-between" mb="md"><Title order={N}>…</Title>{gate && <Button/>}</Group>`
// followed by `<AsOf at={dataUpdatedAt} />`, and each repeated the title in its forbidden and error
// branches as a Group-less `<Title order={N} mb="md">` — a second form of the same header.
// CapaBoardPage carried both forms across three sites and rendered no header at all while loading.
//
// `actions` is passed straight through and is deliberately NOT wrapped. Callers supply it as
// `can("x.create") && <Button/>`, which evaluates to `false` — not `undefined` — for an ungranted
// reader; a wrapper element would give that reader an empty box where the gate is supposed to be
// invisible. Rendering the child as given keeps the denied case byte-identical to the hand-rolled
// headers this replaces.
//
// The heading level is NOT a prop. A register page is the top of its own document — `AppShell`
// renders no heading above it, and neither do the CapaLayout / AuditsLayout / DriftLayout tab
// strips — so the title here is always the page's one `h1`. Making that structural rather than a
// caller's choice is the point: while `order` was a prop, nine adopters took a default of 2 and two
// passed 3, so every register presented an outline with no `h1` at all and the level a page used
// carried no meaning beyond how it was written.
//
// What callers still choose is `size`, which is appearance only. Mantine's `Title` takes `order`
// (which tag) and `size` (which font size) independently, so `order={1} size="h2"` renders an
// `<h1>` that looks exactly like the `<h2>` this replaced. That is what keeps the fix an
// accessibility change and not a retheme: no register moved a pixel.
//
// `updatedAt` is optional because the forbidden and error branches have no loaded query to stamp:
// they pass nothing, and AsOf renders nothing for a null/undefined/0 stamp, so the absent case
// needs no branch here. (features/audits/ProgramPage.tsx is the one hand-rolled register header
// left in the tree — it needs `size="h3"` and no stamp, which this API already supports.)
export function RegisterPageHeader({
  title,
  size = "h2",
  actions,
  updatedAt,
}: {
  title: ReactNode;
  size?: "h2" | "h3";
  actions?: ReactNode;
  updatedAt?: number | null;
}) {
  // The spacing is on the WRAPPER, not on the title row. With `mb` on the Group the gap fell
  // BETWEEN the title and its own freshness stamp, pushing the stamp away from the thing it
  // describes and leaving nothing beneath it — so whatever followed (a scorecard band, a filter
  // row, a table) sat flush against the stamp with no separation at all. One block, one gap below.
  return (
    <Box mb="md">
      <Group justify="space-between">
        <Title order={1} size={size}>
          {title}
        </Title>
        {actions}
      </Group>
      <AsOf at={updatedAt} />
    </Box>
  );
}
