import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CommandPalette } from "./CommandPalette";

function LocationProbe() {
  const loc = useLocation();
  return <main><div data-testid="loc">{loc.pathname + loc.search}</div></main>;
}

function open() {
  return renderWithProviders(
    <>
      <CommandPalette opened onClose={() => {}} />
      <LocationProbe />
    </>,
  );
}

test("typing shows /suggest results; selecting one navigates to the document", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "sop");
  const option = await screen.findByText("Supplier Selection & Evaluation");
  await user.click(option);
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/documents/11111111-1111-1111-1111-111111111111",
    ),
  );
});

test("the footer action opens the full /search results page", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "calibration");
  await user.click(screen.getByText(/Search “calibration” →/));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent("/search?q=calibration"),
  );
});

test("Enter with no selection runs the full search", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "pump{Enter}");
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/search?q=pump"));
});

test("does not jump to a stale suggestion when the query changes after results loaded", async () => {
  const user = userEvent.setup();
  open();
  const input = screen.getByLabelText("Search query");
  await user.type(input, "sop");
  // Suggestions for "sop" have loaded (debounce settled).
  await screen.findByText("Supplier Selection & Evaluation");
  // Type more + Enter before the 150ms debounce catches up: the stale "sop" rows must be
  // suppressed, so Enter runs the full search for the CURRENT text — never the stale doc.
  await user.type(input, "zzz{Enter}");
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/search?q=sopzzz"));
  expect(screen.getByTestId("loc")).not.toHaveTextContent("/documents/");
});

test("has no axe violations when open", async () => {
  open();
  await screen.findByLabelText("Search query");
  expect(await axe(document.body)).toHaveNoViolations();
});
