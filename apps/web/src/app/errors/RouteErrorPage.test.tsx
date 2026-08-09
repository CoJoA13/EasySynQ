import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import { RouteErrorPage } from "./RouteErrorPage";

afterEach(() => vi.restoreAllMocks());

function Tree({ children }: { children: ReactNode }) {
  return (
    <MantineProvider theme={theme}>
      <MemoryRouter>{children}</MemoryRouter>
    </MantineProvider>
  );
}

test("renders fixed safe recovery, focuses the heading, and restores the prior title", async () => {
  document.title = "EasySynQ — Library";
  const rendered = render(<RouteErrorPage onRetry={() => undefined} onReload={() => undefined} />, {
    wrapper: Tree,
  });

  const heading = screen.getByRole("heading", {
    name: "This page couldn't be displayed",
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Page unavailable");
  expect(document.body).not.toHaveTextContent("RAW_ROUTE_ERROR_SENTINEL");
  expect(screen.getByRole("button", { name: "Try this page again" })).toHaveStyle({
    minHeight: "44px",
  });
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute("href", "/");

  rendered.unmount();
  expect(document.title).toBe("EasySynQ — Library");
});

test("retry and reload invoke only their explicit callbacks", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  const onReload = vi.fn();
  render(<RouteErrorPage onRetry={onRetry} onReload={onReload} />, {
    wrapper: Tree,
  });

  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  await user.click(screen.getByRole("button", { name: "Reload EasySynQ" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(onReload).toHaveBeenCalledTimes(1);
});

test("has bounded geometry and no axe violations", async () => {
  const { container } = render(
    <RouteErrorPage onRetry={() => undefined} onReload={() => undefined} />,
    { wrapper: Tree },
  );
  expect(screen.getByRole("region")).toHaveStyle({
    minWidth: "0rem",
    width: "100%",
  });
  expect(await axe(container)).toHaveNoViolations();
});
