import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { ImportPlanBanner } from "./ImportPlanBanner";

test("renders the drift-safe import-plan explainer (the verbatim baseline copy)", async () => {
  const { container } = renderWithProviders(<ImportPlanBanner />);
  expect(screen.getByText("Import plan")).toBeInTheDocument();
  expect(screen.getByText(/Default · drift-safe/)).toBeInTheDocument();
  // the load-bearing drift-safety phrases
  expect(screen.getByText(/Import the current version only/i)).toBeInTheDocument();
  expect(screen.getByText(/Rev A · Effective/)).toBeInTheDocument();
  expect(screen.getByText(/archived as provenance/i)).toBeInTheDocument();
  expect(screen.getByText(/Revision-chain reconstruction is opt-in per family/i)).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("is informational only — no interactive 'Change plan' control (D-6)", () => {
  renderWithProviders(<ImportPlanBanner />);
  expect(screen.queryByRole("button", { name: /change plan/i })).toBeNull();
});
