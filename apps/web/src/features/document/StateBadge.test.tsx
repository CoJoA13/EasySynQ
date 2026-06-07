import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StateBadge } from "./StateBadge";

test("StateBadge renders a text label (status is never color-only)", () => {
  render(
    <MantineProvider>
      <StateBadge state="Effective" />
    </MantineProvider>,
  );
  // The label text carries the meaning + an accessible name — not color alone (DP-7).
  expect(screen.getByText("Effective")).toBeInTheDocument();
  expect(screen.getByLabelText("State: Effective")).toBeInTheDocument();
});

test("StateBadge covers all 7 lifecycle states", () => {
  const states = [
    "Draft",
    "InReview",
    "Approved",
    "Effective",
    "UnderRevision",
    "Superseded",
    "Obsolete",
  ] as const;
  for (const s of states) {
    const { unmount } = render(
      <MantineProvider>
        <StateBadge state={s} />
      </MantineProvider>,
    );
    unmount();
  }
});
