import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { StatLine } from "./StatLine";

import type { ReactElement } from "react";
const wrap = (ui: ReactElement) => render(<MantineProvider>{ui}</MantineProvider>);

it("renders a value + label with a tone glyph and an accessible name", () => {
  wrap(<StatLine value="6 / 8" label="objectives on target" tone="green" />);
  const line = screen.getByLabelText("6 / 8 objectives on target");
  expect(line).toHaveTextContent("6 / 8");
  expect(line).toHaveTextContent("objectives on target");
});

it("renders a label-only status line (no value)", () => {
  wrap(<StatLine label="Mirror & blob integrity — clean" tone="green" />);
  expect(screen.getByLabelText("Mirror & blob integrity — clean")).toBeInTheDocument();
});
