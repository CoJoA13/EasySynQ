import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import type { AuthFailureKind, AuthStatus } from "../../lib/auth";
import { theme } from "../../theme/mantine";
import { AuthStartupScreen, type AuthStartupScreenProps } from "./AuthStartupScreen";

const CASES = [
  ["configuration", "Sign-in is unavailable", "EasySynQ could not connect to its sign-in service."],
  ["callback", "Sign-in was not completed", "Your sign-in response could not be verified."],
  [
    "session",
    "Your session could not be loaded",
    "EasySynQ could not restore your sign-in session.",
  ],
  ["redirect", "Sign-in could not be opened", "EasySynQ could not open the sign-in page."],
  ["timeout", "Sign-in is taking too long", "The sign-in service did not respond in time."],
] as const satisfies ReadonlyArray<readonly [AuthFailureKind, string, string]>;

function loading(
  operation: "bootstrap" | "redirect" = "bootstrap",
): Exclude<AuthStatus, { kind: "ready" }> {
  return { kind: "loading", operation };
}

function failure(kind: AuthFailureKind): Exclude<AuthStatus, { kind: "ready" }> {
  return {
    kind: "error",
    failure: {
      kind,
      recovery: kind === "callback" || kind === "redirect" ? "redirect" : "bootstrap",
    },
  };
}

function renderScreen(
  status: Exclude<AuthStatus, { kind: "ready" }>,
  onRetry: () => Promise<void> = async () => undefined,
  onReload: () => void = vi.fn(),
) {
  function Tree({ children }: { children: ReactNode }) {
    return <MantineProvider theme={theme}>{children}</MantineProvider>;
  }

  return {
    ...render(<AuthStartupScreen status={status} onRetry={onRetry} onReload={onReload} />, {
      wrapper: Tree,
    }),
    onRetry,
    onReload,
  };
}

test.each(["bootstrap", "redirect"] as const)(
  "renders a named pre-shell loading status for %s without recovery actions",
  (operation) => {
    const { container } = renderScreen(loading(operation));

    expect(screen.getByRole("status", { name: "Connecting to sign-in" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "EasySynQ" })).toHaveAttribute(
      "src",
      "/easysynq-mark.svg",
    );
    expect(screen.queryByRole("button", { name: "Try sign-in again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reload EasySynQ" })).not.toBeInTheDocument();
    expect(container.querySelector("nav")).toBeNull();
  },
);

test("uses the approved 24px canvas padding at the narrow breakpoint", () => {
  renderScreen(loading());

  expect(screen.getByRole("main").parentElement).toHaveStyle({
    paddingInline: "var(--mantine-spacing-lg)",
  });
});

test.each(CASES)("renders only approved safe copy for %s failures", (kind, heading, guidance) => {
  const { container } = renderScreen(failure(kind));

  expect(screen.getByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
  expect(screen.getByText(guidance)).toBeInTheDocument();
  expect(container).not.toHaveTextContent(
    "unsafe provider response https://issuer.invalid/?state=secret",
  );
});

test("does not allow raw errors or arbitrary details in the view interface", () => {
  const rawErrorProps = {
    status: failure("configuration"),
    onRetry: async () => undefined,
    onReload: () => undefined,
    // @ts-expect-error Recovery UI only receives classified auth status, never raw exceptions.
    error: new Error("unsafe provider response https://issuer.invalid/?state=secret"),
  } satisfies AuthStartupScreenProps;
  const arbitraryDetailProps = {
    status: failure("configuration"),
    onRetry: async () => undefined,
    onReload: () => undefined,
    // @ts-expect-error Recovery UI only receives classified auth status, never arbitrary details.
    detail: "unsafe provider response https://issuer.invalid/?state=secret",
  } satisfies AuthStartupScreenProps;

  expect(rawErrorProps.error).toBeInstanceOf(Error);
  expect(arbitraryDetailProps.detail).toContain("unsafe provider response");
});

test("focuses the error heading after an error state renders", async () => {
  renderScreen(failure("configuration"));

  const heading = await screen.findByRole("heading", { name: "Sign-in is unavailable" });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(heading).toHaveAttribute("tabindex", "-1");
});

test("keeps retry single-flight and busy until its promise settles", async () => {
  const user = userEvent.setup();
  let resolveRetry: (() => void) | undefined;
  const onRetry = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveRetry = resolve;
      }),
  );
  renderScreen(failure("timeout"), onRetry);

  const retry = screen.getByRole("button", { name: "Try sign-in again" });
  await user.click(retry);
  await user.click(retry);

  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(retry).toBeDisabled();
  expect(retry).toHaveAttribute("aria-busy", "true");
  expect(retry).toHaveStyle({ minHeight: "44px" });

  await act(async () => resolveRetry?.());
  await waitFor(() => expect(retry).toBeEnabled());
  expect(retry).not.toHaveAttribute("aria-busy", "true");
});

test("reload button invokes the injected reload callback and both actions meet target size", async () => {
  const user = userEvent.setup();
  const onReload = vi.fn();
  renderScreen(failure("session"), undefined, onReload);

  const retry = screen.getByRole("button", { name: "Try sign-in again" });
  const reload = screen.getByRole("button", { name: "Reload EasySynQ" });
  await user.click(reload);

  expect(onReload).toHaveBeenCalledTimes(1);
  expect(retry).toHaveStyle({ minHeight: "44px" });
  expect(reload).toHaveStyle({ minHeight: "44px" });
});

test("has no axe violations for loading", async () => {
  const { container } = renderScreen(loading());
  expect(await axe(container)).toHaveNoViolations();
});

test.each(CASES)("has no axe violations for %s recovery", async (kind) => {
  const { container } = renderScreen(failure(kind));
  expect(await axe(container)).toHaveNoViolations();
});
