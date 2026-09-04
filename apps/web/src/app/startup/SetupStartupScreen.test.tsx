import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import {
  SetupStartupScreen,
  type SetupStartupPhase,
  type SetupStartupScreenProps,
  type SetupStartupStatus,
} from "./SetupStartupScreen";

const CASES = [
  [
    "loading",
    "initial",
    "Checking setup status",
    "Please wait while EasySynQ verifies this installation.",
  ],
  [
    "loading",
    "post-finalization",
    "Verifying setup",
    "Setup was saved. EasySynQ is confirming that the installation is ready.",
  ],
  [
    "error",
    "initial",
    "Setup status is unavailable",
    "EasySynQ could not confirm whether this installation is ready. Setup changes are disabled until the status can be verified.",
  ],
  [
    "error",
    "post-finalization",
    "Setup was saved, but could not be verified",
    "Try checking the setup status again. EasySynQ will not repeat finalization.",
  ],
] as const;

function status(kind: SetupStartupStatus["kind"], phase: SetupStartupPhase): SetupStartupStatus {
  return { kind, phase };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: Deferred<T>["resolve"];
  let reject!: Deferred<T>["reject"];
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderScreen(
  startupStatus: SetupStartupStatus,
  onRetry: () => Promise<void> = async () => undefined,
  onReload: () => void = vi.fn(),
) {
  function Tree({ children }: { children: ReactNode }) {
    return <MantineProvider theme={theme}>{children}</MantineProvider>;
  }

  return {
    ...render(<SetupStartupScreen status={startupStatus} onRetry={onRetry} onReload={onReload} />, {
      wrapper: Tree,
    }),
    onRetry,
    onReload,
  };
}

test.each(CASES)(
  "renders safe setup startup copy for %s/%s",
  (kind, phase, labelOrHeading, guidance) => {
    const { container } = renderScreen(status(kind, phase));

    if (kind === "loading") {
      expect(screen.getByRole("status", { name: labelOrHeading })).toBeInTheDocument();
      expect(screen.getByRole("img", { name: "EasySynQ" })).toHaveAttribute(
        "src",
        "/easysynq-mark.svg",
      );
      expect(screen.getByText(guidance)).toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(container.querySelector("nav")).toBeNull();
    } else {
      expect(screen.getByRole("heading", { level: 1, name: labelOrHeading })).toBeInTheDocument();
      expect(screen.getByText(guidance)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Reload EasySynQ" })).toBeInTheDocument();
      expect(
        screen.getByText("If this keeps happening, contact your EasySynQ administrator."),
      ).toBeInTheDocument();
      expect(container).not.toHaveTextContent("unsafe database host https://internal.invalid");
    }
  },
);

test("does not allow raw errors or arbitrary details in the view interface", () => {
  const rawErrorProps = {
    status: status("error", "initial"),
    onRetry: async () => undefined,
    onReload: () => undefined,
    // @ts-expect-error Setup recovery UI receives classified setup status, never raw exceptions.
    error: new Error("unsafe database host https://internal.invalid"),
  } satisfies SetupStartupScreenProps;
  const arbitraryDetailProps = {
    status: status("error", "initial"),
    onRetry: async () => undefined,
    onReload: () => undefined,
    // @ts-expect-error Setup recovery UI receives classified setup status, never arbitrary details.
    detail: "unsafe database host https://internal.invalid",
  } satisfies SetupStartupScreenProps;

  expect(rawErrorProps.error).toBeInstanceOf(Error);
  expect(arbitraryDetailProps.detail).toContain("unsafe database host");
});

test("focuses the error heading after an error state renders", async () => {
  renderScreen(status("error", "initial"));

  const heading = await screen.findByRole("heading", { name: "Setup status is unavailable" });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(heading).toHaveAttribute("tabindex", "-1");
});

test("keeps retry single-flight and busy until its promise settles", async () => {
  const user = userEvent.setup();
  const retry = deferred<void>();
  const onRetry = vi.fn(() => retry.promise);
  renderScreen(status("error", "initial"), onRetry);

  const button = screen.getByRole("button", { name: "Try again" });
  await user.click(button);
  await user.click(button);

  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(button).toHaveStyle({ minHeight: "44px" });

  await act(async () => retry.resolve());
  await waitFor(() => expect(button).toBeEnabled());
  expect(button).not.toHaveAttribute("aria-busy", "true");
});

test("reload button invokes the injected reload callback and both actions meet target size", async () => {
  const user = userEvent.setup();
  const onReload = vi.fn();
  renderScreen(status("error", "post-finalization"), undefined, onReload);

  const retry = screen.getByRole("button", { name: "Try again" });
  const reload = screen.getByRole("button", { name: "Reload EasySynQ" });
  await user.click(reload);

  expect(onReload).toHaveBeenCalledTimes(1);
  expect(retry).toHaveStyle({ minHeight: "44px" });
  expect(reload).toHaveStyle({ minHeight: "44px" });
});

test("uses the approved lg canvas padding at the narrow breakpoint", () => {
  renderScreen(status("loading", "initial"));

  expect(screen.getByRole("main").parentElement).toHaveStyle({
    paddingInline: "var(--mantine-spacing-lg)",
  });
});

test("constrains the startup panel width while allowing it to shrink", () => {
  renderScreen(status("loading", "initial"));

  const panel = screen.getByRole("main");
  expect(panel).toHaveStyle({
    maxWidth: "calc(27.5rem * var(--mantine-scale))",
    minWidth: "0px",
  });
});

test.each(CASES)("has no axe violations for %s/%s", async (kind, phase) => {
  const { container } = renderScreen(status(kind, phase));
  expect(await axe(container)).toHaveNoViolations();
});
