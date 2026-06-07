import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { LibraryPage } from "./LibraryPage";

test("LibraryPage lists documents and opens a row in the drawer", async () => {
  const { container } = renderWithProviders(<LibraryPage />, { route: "/library" });

  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());
  // state badge + clause chip render
  expect(screen.getByText("Effective")).toBeInTheDocument();
  expect(screen.getByText("8.4")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();

  // clicking a row opens the detail drawer with the document title as a heading
  await userEvent.click(screen.getByText("SOP-PUR-014"));
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Supplier Selection & Evaluation" }),
    ).toBeInTheDocument(),
  );
});
