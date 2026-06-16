import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import { TONE_GLYPH } from "../../lib/status";
import type { Capa } from "../../lib/types";
import { CapaCard } from "./CapaCard";

const capa: Capa = {
  id: "ca1",
  identifier: "REC-000031",
  title: "Supplier re-evaluation overdue",
  source: "audit",
  severity: "Major",
  process_id: null,
  close_state: "RootCause",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: null,
  created_at: "2026-05-20T09:00:00+00:00",
};

function wrap(c: Capa, onOpen = vi.fn()) {
  render(
    <MantineProvider theme={theme}>
      <CapaCard capa={c} onOpen={onOpen} />
    </MantineProvider>,
  );
  return onOpen;
}

test("shows identifier, title, severity and source", () => {
  wrap(capa);
  expect(screen.getByText("REC-000031")).toBeInTheDocument();
  expect(screen.getByText("Supplier re-evaluation overdue")).toBeInTheDocument();
  // Severity rides the canonical StatusBadge: a text label, an accessible name, and a non-colour glyph
  // (Major → warning ◔) so it survives colour-blindness / a greyscale audit export (DP-7).
  expect(screen.getByText("Major")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Major")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(screen.getByText("Audit")).toBeInTheDocument();
});

test("calls onOpen with the capa id when activated", async () => {
  const onOpen = wrap(capa);
  await userEvent.click(screen.getByRole("button", { name: /REC-000031/ }));
  expect(onOpen).toHaveBeenCalledWith("ca1");
});

test("a Rejected card shows the Rejected marker", () => {
  wrap({ ...capa, close_state: "Rejected" });
  expect(screen.getByText("Rejected")).toBeInTheDocument();
});
