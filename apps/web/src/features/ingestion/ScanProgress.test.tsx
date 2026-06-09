import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { ScanProgress } from "./ScanProgress";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("a Scanning run shows the human stage label, the caption, and a Cancel button", async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  renderWithProviders(<ScanProgress run={runWith({ status: "Scanning" })} onCancel={onCancel} />);
  // label now appears in both the heading and the stepper step marker
  expect(screen.getAllByText("Scanning files").length).toBeGreaterThan(0);
  expect(screen.getByText(/Scanning…/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Cancel import" }));
  expect(onCancel).toHaveBeenCalled();
});

test("a Classifying run names the classify stage", () => {
  renderWithProviders(<ScanProgress run={runWith({ status: "Classifying" })} onCancel={() => {}} />);
  // label now appears in both the heading and the stepper step marker
  expect(screen.getAllByText("Classifying content").length).toBeGreaterThan(0);
});

test("the Cancel button is hidden when no onCancel is provided (lacks import.execute)", () => {
  renderWithProviders(<ScanProgress run={runWith({ status: "Scanning" })} />);
  expect(screen.getAllByText("Scanning files").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Cancel import" })).not.toBeInTheDocument();
});

test("a Failed run shows a calm error alert with run.error (no Cancel)", () => {
  renderWithProviders(
    <ScanProgress run={runWith({ status: "Failed", error: "extractor crashed on broken.bin" })} onCancel={() => {}} />,
  );
  expect(screen.getByText(/extractor crashed on broken.bin/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel import" })).not.toBeInTheDocument();
});

test("an unknown additive stage degrades calmly (no crash)", () => {
  renderWithProviders(<ScanProgress run={runWith({ status: "Renaming" })} onCancel={() => {}} />);
  // "Working…" appears in both the heading and the caption fallback — assert at least one match
  expect(screen.getAllByText(/Working…/).length).toBeGreaterThan(0);
});

test("has no axe violations (scanning + failed)", async () => {
  const scanning = renderWithProviders(
    <ScanProgress run={runWith({ status: "Scanning" })} onCancel={() => {}} />,
  );
  expect(await axe(scanning.container)).toHaveNoViolations();
  scanning.unmount();
  const failed = renderWithProviders(
    <ScanProgress run={runWith({ status: "Failed", error: "boom" })} onCancel={() => {}} />,
  );
  expect(await axe(failed.container)).toHaveNoViolations();
});
