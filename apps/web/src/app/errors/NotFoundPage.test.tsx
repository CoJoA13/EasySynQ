import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { NotFoundPage } from "./NotFoundPage";

function LocationProbe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function Tree({ children }: { children: ReactNode }) {
  return (
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/missing/private-segment"]}>{children}</MemoryRouter>
    </MantineProvider>
  );
}

test("renders fixed safe 404 copy, focus, targets, and no raw pathname", async () => {
  const { container } = render(<NotFoundPage />, { wrapper: Tree });
  const heading = screen.getByRole("heading", { name: "Page not found" });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(
    screen.getByText("The page you requested isn't available in EasySynQ."),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "Open document library" })).toHaveAttribute(
    "href",
    "/library",
  );
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveStyle({
    minHeight: "44px",
  });
  expect(container).not.toHaveTextContent("private-segment");
  expect(await axe(container)).toHaveNoViolations();
});

test("safe links navigate to exact internal destinations", async () => {
  const user = userEvent.setup();
  render(
    <>
      <NotFoundPage />
      <LocationProbe />
    </>,
    { wrapper: Tree },
  );
  await user.click(screen.getByRole("link", { name: "Open document library" }));
  expect(screen.getByLabelText("location")).toHaveTextContent("/library");
});
