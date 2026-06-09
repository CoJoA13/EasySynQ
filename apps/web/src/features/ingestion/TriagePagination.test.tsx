import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { FILES_PAGE_SIZE } from "./filters";
import { TriagePagination } from "./TriagePagination";

test("Prev is disabled at offset 0; Next disabled when !hasMore", () => {
  renderWithProviders(
    <TriagePagination offset={0} hasMore={false} onOffset={() => {}} />,
  );
  expect(screen.getByRole("button", { name: /Prev/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
});

test("clicking Next advances the offset by FILES_PAGE_SIZE", async () => {
  const user = userEvent.setup();
  const onOffset = vi.fn();
  renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={onOffset} />,
  );
  await user.click(screen.getByRole("button", { name: /Next/ }));
  expect(onOffset).toHaveBeenCalledWith(FILES_PAGE_SIZE);
});

test("clicking Prev steps back one page, clamped at 0", async () => {
  const user = userEvent.setup();
  const onOffset = vi.fn();
  renderWithProviders(
    <TriagePagination offset={FILES_PAGE_SIZE} hasMore onOffset={onOffset} />,
  );
  await user.click(screen.getByRole("button", { name: /Prev/ }));
  expect(onOffset).toHaveBeenCalledWith(0);
});

test("shows 'Showing X–Y of N' when total is provided (page 2, 3 rows on page)", () => {
  // offset=100, 3 rows on this page (hasMore false), total 103 → "Showing 101–103 of 103"
  renderWithProviders(
    <TriagePagination offset={FILES_PAGE_SIZE} hasMore={false} onOffset={() => {}} pageCount={3} total={103} />,
  );
  expect(screen.getByText(/Showing 101–103 of 103/)).toBeInTheDocument();
});

test("omits the count line when total is undefined", () => {
  renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={() => {}} pageCount={100} />,
  );
  expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(
    <TriagePagination offset={0} hasMore onOffset={() => {}} pageCount={100} total={281} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
