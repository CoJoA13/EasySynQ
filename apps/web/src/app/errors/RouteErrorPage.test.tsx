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
    minWidth: "0px",
    width: "100%",
  });
  expect(await axe(container)).toHaveNoViolations();
});

test("[U15] a failed chunk load leads with reload, not a retry that can never work", async () => {
  // React.lazy memoizes a rejected payload, so remounting re-reads the same rejection — the
  // stale-chunk-after-deploy case would leave "Try this page again" a permanent no-op.
  const onRetry = vi.fn();
  const onReload = vi.fn();
  render(
    <RouteErrorPage
      onRetry={onRetry}
      onReload={onReload}
      error={new TypeError("Failed to fetch dynamically imported module: /assets/Records-abc.js")}
    />,
    { wrapper: Tree },
  );
  expect(screen.queryByRole("button", { name: /try this page again/i })).toBeNull();
  expect(screen.getByText(/probably updated while your tab was open/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /reload easysynq/i }));
  expect(onReload).toHaveBeenCalledTimes(1);
  expect(onRetry).not.toHaveBeenCalled();
});

test("[U15] an ordinary render fault still offers the in-place retry", async () => {
  const onRetry = vi.fn();
  render(<RouteErrorPage onRetry={onRetry} error={new Error("boom")} />, { wrapper: Tree });
  await userEvent.click(screen.getByRole("button", { name: /try this page again/i }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});
