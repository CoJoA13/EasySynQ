import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { StateBadge } from "./StateBadge";

test("StateBadge renders the human label for a state", () => {
  render(
    <MantineProvider>
      <StateBadge state="UnderRevision" />
    </MantineProvider>,
  );
  expect(screen.getByText("Under revision")).toBeInTheDocument();
});
