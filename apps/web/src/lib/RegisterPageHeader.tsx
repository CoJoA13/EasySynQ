import { Group, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { AsOf } from "./AsOf";

// The shared register page header: the title, its optional write affordance, and the freshness
// stamp beneath. Eleven register pages hand-rolled the identical
// `<Group justify="space-between" mb="md"><Title order={N}>…</Title>{gate && <Button/>}</Group>`
// followed by `<AsOf at={dataUpdatedAt} />`, and several rendered a DIVERGENT second copy in their
// forbidden and error branches (CapaBoardPage carried three different forms of its own header).
//
// `actions` is passed straight through and is deliberately NOT wrapped. Callers supply it as
// `can("x.create") && <Button/>`, which evaluates to `false` — not `undefined` — for an ungranted
// reader; a wrapper element would give that reader an empty box where the gate is supposed to be
// invisible. Rendering the child as given keeps the denied case byte-identical to the hand-rolled
// headers this replaces.
//
// `order` stays a prop rather than being normalised. Register titles currently run at 1, 2 and 3
// and several suites pin the level (AuditsListPage asserts `{ level: 2, name: "Internal audit" }`),
// so levelling them is an accessibility change with its own test surface — recorded as a residual
// for the programme's a11y pass, not smuggled into a retheme slice.
//
// `updatedAt` is optional because not every adopter has a query stamp (the audit programme's plans
// header has none). AsOf itself renders nothing for a null/undefined/0 stamp, so the absent case
// needs no branch here.
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
