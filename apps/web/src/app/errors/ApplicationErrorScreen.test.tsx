import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import { ApplicationErrorBoundary } from "./ApplicationErrorBoundary";
import { ApplicationErrorScreen } from "./ApplicationErrorScreen";

afterEach(() => vi.restoreAllMocks());

function Tree({ children }: { children: ReactNode }) {
  return <MantineProvider theme={theme}>{children}</MantineProvider>;
}

function BrokenApplication(): never {
  throw new Error("RAW_GLOBAL_ERROR_SENTINEL RAW_GLOBAL_PATH_SENTINEL");
}

test("renders safe full-screen recovery without router context or raw details", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const onReload = vi.fn();
  const { container } = render(
    <ApplicationErrorBoundary fallback={() => <ApplicationErrorScreen onReload={onReload} />}>
      <BrokenApplication />
    </ApplicationErrorBoundary>,
    { wrapper: Tree },
  );

  const heading = screen.getByRole("heading", {
    name: "EasySynQ couldn't be displayed",
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(screen.getByRole("button", { name: "Reload EasySynQ" })).toHaveStyle({
    minHeight: "44px",
  });
  expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute("href", "/");
  expect(container).not.toHaveTextContent("RAW_GLOBAL_ERROR_SENTINEL");
  expect(container).not.toHaveTextContent("RAW_GLOBAL_PATH_SENTINEL");
  expect(await axe(container)).toHaveNoViolations();
});

test("reload invokes exactly the injected browser seam", async () => {
  const user = userEvent.setup();
  const onReload = vi.fn();
  render(<ApplicationErrorScreen onReload={onReload} />, { wrapper: Tree });

  await user.click(screen.getByRole("button", { name: "Reload EasySynQ" }));
  expect(onReload).toHaveBeenCalledTimes(1);
});

test("uses bounded narrow-screen geometry", () => {
  render(<ApplicationErrorScreen onReload={() => undefined} />, { wrapper: Tree });

  const main = screen.getByRole("main");
  expect(main.parentElement).toHaveStyle({
    paddingInline: "var(--mantine-spacing-lg)",
  });
  expect(main).toHaveStyle({ minWidth: "0px", width: "100%" });
});
