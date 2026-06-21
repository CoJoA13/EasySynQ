import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { interestedPartyListFixture } from "../../test/msw/handlers";
import { InterestedPartyTypeBoard } from "./InterestedPartyTypeBoard";

const ROWS = interestedPartyListFixture.data;

it("renders the 7 party-type cards (incl. empty ones) and a labelled region", async () => {
  const { container } = renderWithProviders(
    <InterestedPartyTypeBoard rows={ROWS} selectedId={null} onSelect={() => {}} />,
  );
  expect(
    screen.getByRole("region", { name: "Interested parties by type — 6 parties" }),
  ).toBeInTheDocument();
  // populated cards
  expect(screen.getByRole("group", { name: "Customers, 3 parties" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Regulators, 1 party" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Suppliers, 1 party" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Partners, 1 party" })).toBeInTheDocument();
  // an empty card still renders (the completeness prompt across the spine)
  const employees = screen.getByRole("group", { name: "Employees, 0 parties" });
  expect(within(employees).getByText("None recorded.")).toBeInTheDocument();
  // the closed party folds its status INTO the accessible name — an explicit aria-label swallows the
  // nested "Closed" badge per the ARIA name computation, so closed must not be strikethrough/dim alone
  // (DP-5 / WCAG 2.2 AA). A sighted user sees the badge; AT hears "(closed)".
  const customers = screen.getByRole("group", { name: "Customers, 3 parties" });
  expect(within(customers).getByText("Closed")).toBeInTheDocument();
  expect(
    within(customers).getByRole("button", {
      name: "Customer: Former distributor (legacy) — Low influence (closed)",
    }),
  ).toBeInTheDocument();
  // an unspecified-influence chip carries "influence unspecified" in its accessible name
  expect(
    screen.getByRole("button", {
      name: "Partner: Regional logistics partner — influence unspecified",
    }),
  ).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("opens a party when its chip is clicked (the chip's name carries the party type + influence)", async () => {
  const onSelect = vi.fn();
  const user = userEvent.setup();
  renderWithProviders(
    <InterestedPartyTypeBoard rows={ROWS} selectedId={null} onSelect={onSelect} />,
  );
  await user.click(
    screen.getByRole("button", { name: "Customer: Acme Manufacturing — High influence" }),
  );
  expect(onSelect).toHaveBeenCalledWith("ee000001-0001-0001-0001-000000000001");
});

it("renders all 7 cards as empty and no chips for an empty register", () => {
  renderWithProviders(<InterestedPartyTypeBoard rows={[]} selectedId={null} onSelect={() => {}} />);
  expect(
    screen.getByRole("region", { name: "Interested parties by type — 0 parties" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("None recorded.")).toHaveLength(7);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
