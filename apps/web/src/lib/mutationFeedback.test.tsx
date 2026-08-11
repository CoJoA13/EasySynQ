import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { useEffect } from "react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { ApiError } from "./api";
import {
  isRetryableMutationError,
  MutationFeedbackOutlet,
  useMutationFeedback,
} from "./mutationFeedback";

test.each([
  [new TypeError("network"), true],
  [new ApiError(408, "timeout", "Timed out"), true],
  [new ApiError(429, "rate_limited", "Slow down"), true],
  [new ApiError(500, "error", "Unavailable"), true],
  [new ApiError(599, "error", "Unavailable"), true],
  [new ApiError(400, "bad", "Bad request"), false],
  [new ApiError(401, "unauthorized", "Unauthorized"), false],
  [new ApiError(403, "forbidden", "Forbidden"), false],
  [new ApiError(404, "not_found", "Missing"), false],
  [new ApiError(409, "conflict", "Conflict"), false],
  [new ApiError(422, "invalid", "Invalid"), false],
  [new Error("programming"), false],
  [undefined, false],
])("classifies mutation retry eligibility", (error, expected) => {
  expect(isRetryableMutationError(error)).toBe(expected);
});

type Feedback = ReturnType<typeof useMutationFeedback>;

function Harness({ onReady }: { onReady: (feedback: Feedback) => void }) {
  const feedback = useMutationFeedback();

  useEffect(() => {
    onReady(feedback);
  }, [feedback, onReady]);

  return <MutationFeedbackOutlet />;
}

function renderFeedbackHarness() {
  let feedback: Feedback | undefined;
  const rendered = renderWithProviders(<Harness onReady={(next) => (feedback = next)} />);

  expect(feedback).toBeDefined();
  return {
    ...rendered,
    feedback: () => {
      if (!feedback) throw new Error("Mutation feedback provider was not ready");
      return feedback;
    },
  };
}

function reportTwoFailures(feedback: Feedback) {
  const firstRetry = vi.fn(async () => undefined);
  const secondRetry = vi.fn(async () => undefined);

  act(() => {
    feedback.report({
      key: "mark-read:n1",
      title: "Couldn't mark First read",
      error: new ApiError(503, "down", "Service unavailable"),
      retry: firstRetry,
      retryLabel: "Try marking First read again",
      dismissLabel: "Dismiss mark-read error for First",
      successMessage: "Notification marked read",
    });
    feedback.report({
      key: "mark-read:n2",
      title: "Couldn't mark Second read",
      error: new ApiError(503, "down", "Service unavailable"),
      retry: secondRetry,
      retryLabel: "Try marking Second read again",
      dismissLabel: "Dismiss mark-read error for Second",
      successMessage: "Notification marked read",
    });
  });

  return { firstRetry, secondRetry };
}

test("retains distinct keyed errors, replaces a matching key, and dismisses only that entry", async () => {
  const user = userEvent.setup();
  const { feedback } = renderFeedbackHarness();
  reportTwoFailures(feedback());

  expect(screen.getAllByRole("alert")).toHaveLength(2);
  expect(screen.getByText("Couldn't mark First read")).toBeInTheDocument();
  expect(screen.getByText("Couldn't mark Second read")).toBeInTheDocument();

  act(() => {
    feedback().report({
      key: "mark-read:n1",
      title: "Couldn't mark First read again",
      error: new ApiError(503, "down", "Still unavailable"),
      dismissLabel: "Dismiss mark-read error for First",
    });
  });

  expect(screen.getAllByRole("alert")).toHaveLength(2);
  expect(screen.queryByText("Couldn't mark First read")).not.toBeInTheDocument();
  expect(screen.getByText("Couldn't mark First read again")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Try marking First read again" }),
  ).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Dismiss mark-read error for First" }));
  expect(screen.queryByText("Couldn't mark First read again")).not.toBeInTheDocument();
  expect(screen.getByText("Couldn't mark Second read")).toBeInTheDocument();
});

test("retries one retained callback at a time, clears it on success, and announces the outcome", async () => {
  const user = userEvent.setup();
  let resolveRetry: (() => void) | undefined;
  const retry = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveRetry = resolve;
      }),
  );
  const { feedback } = renderFeedbackHarness();

  act(() => {
    feedback().report({
      key: "mark-read:n1",
      title: "Couldn't mark First read",
      error: new ApiError(503, "down", "Service unavailable"),
      retry,
      retryLabel: "Try marking First read again",
      dismissLabel: "Dismiss mark-read error for First",
      successMessage: "Notification marked read",
    });
  });

  const retryButton = screen.getByRole("button", { name: "Try marking First read again" });
  await user.click(retryButton);
  await user.click(retryButton);
  expect(retry).toHaveBeenCalledTimes(1);
  expect(retryButton).toBeDisabled();

  act(() => resolveRetry?.());
  await waitFor(() =>
    expect(
      screen.queryByRole("alert", { name: /Couldn't mark First read/i }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.getByRole("status")).toHaveTextContent("Notification marked read");
});

test("creates a new live-region event for identical success announcements", async () => {
  const user = userEvent.setup();
  const { feedback } = renderFeedbackHarness();
  reportTwoFailures(feedback());
  const status = screen.getByRole("status");
  const announcementEvents: string[] = [];
  const observer = new MutationObserver(() => {
    const message = status.textContent?.trim();
    if (message) announcementEvents.push(message);
  });
  observer.observe(status, { childList: true, characterData: true, subtree: true });

  try {
    await user.click(screen.getByRole("button", { name: "Try marking First read again" }));
    await waitFor(() => expect(announcementEvents).toEqual(["Notification marked read"]));

    await user.click(screen.getByRole("button", { name: "Try marking Second read again" }));
    await waitFor(() =>
      expect(announcementEvents).toEqual(["Notification marked read", "Notification marked read"]),
    );
  } finally {
    observer.disconnect();
  }
});

test("adds stable safe ordinal context when retained notification titles collide", () => {
  const { feedback } = renderFeedbackHarness();

  act(() => {
    for (const key of ["mark-read:private-id-a", "mark-read:private-id-b"]) {
      feedback().report({
        key,
        title: "This notification remains unread: Quarterly review",
        error: new ApiError(503, "down", "Service unavailable"),
        retry: vi.fn(async () => undefined),
        retryLabel: "Try marking Quarterly review read again",
        dismissLabel: "Dismiss mark-read error for Quarterly review",
      });
    }
  });

  expect(
    screen.getByText("This notification remains unread: Quarterly review (1 of 2)"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("This notification remains unread: Quarterly review (2 of 2)"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Try marking Quarterly review read again (1 of 2)" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Try marking Quarterly review read again (2 of 2)" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "Dismiss mark-read error for Quarterly review (1 of 2)",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "Dismiss mark-read error for Quarterly review (2 of 2)",
    }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("private-id-a");
  expect(document.body).not.toHaveTextContent("private-id-b");
});

test("removes retry after a non-retryable retry failure while keeping safe API copy", async () => {
  const user = userEvent.setup();
  const retry = vi.fn(async () => {
    throw new ApiError(404, "not_found", "Notification no longer exists");
  });
  const { feedback } = renderFeedbackHarness();

  act(() => {
    feedback().report({
      key: "mark-read:n1",
      title: "Couldn't mark First read",
      error: new ApiError(503, "down", "Service unavailable"),
      retry,
      retryLabel: "Try marking First read again",
      dismissLabel: "Dismiss mark-read error for First",
    });
  });

  await user.click(screen.getByRole("button", { name: "Try marking First read again" }));
  await waitFor(() =>
    expect(screen.getByText("Notification no longer exists")).toBeInTheDocument(),
  );
  expect(
    screen.queryByRole("button", { name: "Try marking First read again" }),
  ).not.toBeInTheDocument();
  expect(retry).toHaveBeenCalledTimes(1);
});

test("two simultaneous mutation feedback entries have no axe violations", async () => {
  const { container, feedback } = renderFeedbackHarness();
  reportTwoFailures(feedback());

  expect(await axe(container)).toHaveNoViolations();
});
