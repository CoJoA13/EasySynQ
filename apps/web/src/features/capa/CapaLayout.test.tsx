import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaLayout } from "./CapaLayout";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<div>BOARD FACE</div>} />
        <Route path="complaints" element={<div>COMPLAINTS FACE</div>} />
        <Route path="ncrs" element={<div>NCRS FACE</div>} />
      </Route>
    </Routes>
  );
}

function tabContainerSize() {
  const container = screen.getByRole("tablist").closest(".mantine-Container-root");
  expect(container).not.toBeNull();
  return (container as HTMLElement).style.getPropertyValue("--container-size");
}

// The strip's width is CONSTANT across the three faces, and that is the whole point of pinning it.
// It used to follow the active tab — `xl` on the board, `lg` on the two list faces — which kept it
// aligned with each face's own content but made the strip itself JUMP 90px sideways whenever the
// user changed tab, because a Mantine Container is centred and lg/xl differ by 180px. The owner
// reported that movement from the running application. Unifying every CAPA face at `xl` satisfies
// both readings at once: the strip stays aligned with its content AND never moves. It also matches
// AuditsLayout (constant `xl`, both children `xl`) and DriftLayout (constant `lg`, both children
// `lg`) — CAPA was the only one of the three tab sections that varied.
const STRIP_WIDTH = "var(--container-size-xl)";

test("renders the board face + three tabs at /capa", async () => {
  renderWithProviders(tree(), { route: "/capa" });
  expect(await screen.findByText("BOARD FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Board" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Complaints" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toBeInTheDocument();
  expect(tabContainerSize()).toBe(STRIP_WIDTH);
});

test("the active tab follows the deep-linked route", async () => {
  renderWithProviders(tree(), { route: "/capa/ncrs" });
  expect(await screen.findByText("NCRS FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toHaveAttribute("aria-selected", "true");
  expect(tabContainerSize()).toBe(STRIP_WIDTH);
});

test("clicking a tab navigates to that face", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  await screen.findByText("BOARD FACE");
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("COMPLAINTS FACE")).toBeInTheDocument();
  expect(tabContainerSize()).toBe(STRIP_WIDTH);
});

// The regression guard proper. Each assertion above only proves one face in isolation; this is the
// one that fails if the width ever becomes a function of the active tab again, which is how the
// strip came to move in the first place.
test("the tab strip does not move when the face changes", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  await screen.findByText("BOARD FACE");
  const onBoard = tabContainerSize();

  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("COMPLAINTS FACE")).toBeInTheDocument();
  const onComplaints = tabContainerSize();

  await u.click(screen.getByRole("tab", { name: "NCRs" }));
  expect(await screen.findByText("NCRS FACE")).toBeInTheDocument();
  const onNcrs = tabContainerSize();

  expect([onBoard, onComplaints, onNcrs]).toEqual([STRIP_WIDTH, STRIP_WIDTH, STRIP_WIDTH]);
});
