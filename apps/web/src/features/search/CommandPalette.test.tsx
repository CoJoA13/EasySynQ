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

test("has no axe violations when open", async () => {
  open();
  await screen.findByLabelText("Search query");
  expect(await axe(document.body)).toHaveNoViolations();
});
