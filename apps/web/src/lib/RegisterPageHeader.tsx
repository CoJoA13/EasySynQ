import { Group, Title } from "@mantine/core";
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
// `order` stays a prop rather than being normalised. The eleven adopters run at 2 and 3, and
// AuditsListPage.test.tsx pins `{ level: 2, name: "Internal audit" }` AND uses it as its load gate,
// so levelling them is an accessibility change with its own test surface — recorded as
// RES-REGISTER-HEADING-LEVELS for the programme's a11y pass, not smuggled into a retheme slice.
// The union is `2 | 3` because that is what the adopters use; the `order={1}` registers (Library,
// Reports, Ingestion) and ProgrammePage's `order={4}` sub-heading each widen it by one line.
//
// `updatedAt` is optional because the forbidden and error branches have no loaded query to stamp:
// they pass nothing, and AsOf renders nothing for a null/undefined/0 stamp, so the absent case
// needs no branch here. (features/audits/ProgrammePage.tsx is the one hand-rolled register header
// left in the tree — it needs `order={3}` and no stamp, which this API already supports.)
export function RegisterPageHeader({
  title,
  order = 2,
  actions,
  updatedAt,
}: {
  title: ReactNode;
  order?: 2 | 3;
  actions?: ReactNode;
  updatedAt?: number | null;
}) {
  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={order}>{title}</Title>
        {actions}
      </Group>
      <AsOf at={updatedAt} />
    </>
  );
}
