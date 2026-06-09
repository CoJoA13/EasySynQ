import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { IngestionFacetBar } from "./IngestionFacetBar";

test("renders the four confidence options (All / High / Medium / Low)", () => {
  renderWithProviders(<IngestionFacetBar conf="ALL" onConf={() => {}} />);
  for (const label of ["All", "High", "Medium", "Low"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("selecting High reports the HIGH band via onConf", async () => {
  const user = userEvent.setup();
  const onConf = vi.fn();
  renderWithProviders(<IngestionFacetBar conf="ALL" onConf={onConf} />);
  await user.click(screen.getByText("High"));
  expect(onConf).toHaveBeenCalledWith("HIGH");
});

test("reflects the current confidence from `conf`", () => {
  renderWithProviders(<IngestionFacetBar conf="MEDIUM" onConf={() => {}} />);
  // the Medium radio is the checked option of the segmented control
  expect(screen.getByRole("radio", { name: "Medium" })).toBeChecked();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <IngestionFacetBar conf="ALL" onConf={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
