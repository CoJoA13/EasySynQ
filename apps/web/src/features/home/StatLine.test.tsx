import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { StatLine } from "./StatLine";

import type { ReactElement } from "react";
const wrap = (ui: ReactElement) => render(<MantineProvider>{ui}</MantineProvider>);

it("renders a value + label with a tone glyph and an accessible name", () => {
  wrap(<StatLine value="6 / 8" label="objectives on target" tone="green" />);
  const line = screen.getByRole("group", { name: "6 / 8 objectives on target" });
  expect(line).toHaveTextContent("6 / 8");
  expect(line).toHaveTextContent("objectives on target");
  expect(line).toHaveAccessibleDescription("Status: On track");
});

it("renders a label-only status line (no value)", () => {
  wrap(<StatLine label="Mirror & blob integrity — clean" tone="green" />);
  const line = screen.getByRole("group", { name: "Mirror & blob integrity — clean" });
  expect(line).toHaveAccessibleDescription("Status: On track");
});

it("announces RAG meaning rather than a raw colour word", () => {
  wrap(<StatLine value={2} label="document reviews overdue" tone="amber" />);
  const line = screen.getByRole("group", { name: "2 document reviews overdue" });
  expect(line).toHaveAccessibleDescription("Status: Needs attention");
  expect(screen.queryByLabelText(/amber/i)).not.toBeInTheDocument();
});
