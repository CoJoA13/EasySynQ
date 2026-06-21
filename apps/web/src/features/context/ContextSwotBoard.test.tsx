import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { contextListFixture } from "../../test/msw/handlers";
import { ContextSwotBoard } from "./ContextSwotBoard";

const ROWS = contextListFixture.data;

it("renders the four SWOT quadrants, the uncategorized strip, and a labelled region", async () => {
  const { container } = renderWithProviders(
    <ContextSwotBoard rows={ROWS} selectedId={null} onSelect={() => {}} />,
  );
  expect(
    screen.getByRole("region", { name: "SWOT analysis of 5 context issues" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Strengths, 1 issue" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Weaknesses, 1 issue" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Opportunities, 1 issue" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Threats, 1 issue" })).toBeInTheDocument();
  // the null-category closed row falls to the Uncategorized overflow strip, de-emphasized (Closed badge)
  const strip = screen.getByRole("group", { name: "Uncategorized, 1 issue" });
  expect(within(strip).getByText("Pending reorganisation of the QA function")).toBeInTheDocument();
  expect(within(strip).getByText("Closed")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("opens an issue when its chip is clicked (the chip's name carries the classification)", async () => {
  const onSelect = vi.fn();
  const user = userEvent.setup();
  renderWithProviders(<ContextSwotBoard rows={ROWS} selectedId={null} onSelect={onSelect} />);
  // the chip's accessible name is "{classification}: {description}" (distinct from the table anchors)
  await user.click(screen.getByRole("button", { name: "Internal: Skilled and certified QA team" }));
  expect(onSelect).toHaveBeenCalledWith("cc000001-0001-0001-0001-000000000001");
});

it("shows a calm empty quadrant message and no uncategorized strip when empty", () => {
  renderWithProviders(<ContextSwotBoard rows={[]} selectedId={null} onSelect={() => {}} />);
  expect(
    screen.getByRole("region", { name: "SWOT analysis of 0 context issues" }),
  ).toBeInTheDocument();
  expect(screen.getByText("No strengths recorded.")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: /Uncategorized/ })).not.toBeInTheDocument();
});
