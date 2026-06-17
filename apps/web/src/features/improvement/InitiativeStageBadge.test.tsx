import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, it } from "vitest";
import type { InitiativeStage } from "../../lib/types";
import { InitiativeStageBadge } from "./InitiativeStageBadge";

function renderBadge(stage: InitiativeStage) {
  return render(
    <MantineProvider>
      <InitiativeStageBadge stage={stage} />
    </MantineProvider>,
  );
}

it.each([
  ["Open", "Open"],
  ["InProgress", "In progress"],
  ["Completed", "Completed"],
  ["Closed", "Closed"],
  ["Cancelled", "Cancelled"],
] as const)("renders %s with a label + a distinct State aria-name", (stage, label) => {
  renderBadge(stage);
  // The visible label carries the meaning (DP-5) and the aria-name is kind-prefixed + distinct.
  expect(screen.getByText(label)).toBeInTheDocument();
  expect(screen.getByLabelText(`State: ${label}`)).toBeInTheDocument();
});
