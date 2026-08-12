// apps/web/src/features/notifications/NotificationsPage.test.tsx
import { act, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationsPage } from "./NotificationsPage";

describe("NotificationsPage", () => {
  it("lists notifications", async () => {
    const { container } = renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("Review requested: SOP-001")).toBeInTheDocument();
    expect(screen.getByText("CAPA assigned: CAPA-002")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows the empty state when there is nothing", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("You're all caught up.")).toBeInTheDocument();
  });

  it("shows a retryable error state on failure", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("Couldn't load notifications")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps a failed mark-all read on the page and retries the same operation", async () => {
    const user = userEvent.setup();
    let requests = 0;
    let finishRetry: ((response: Response) => void) | undefined;
    server.use(
      http.post("/api/v1/notifications/read-all", () => {
        requests += 1;
        if (requests === 1) {
          return HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 });
        }
        return new Promise<Response>((resolve) => {
          finishRetry = resolve;
        });
      }),
    );

    const { container } = renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    await user.click(await screen.findByRole("button", { name: "Mark all read" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't mark notifications read");
    expect(await axe(container)).toHaveNoViolations();
    expect(screen.getByRole("button", { name: "Mark all read" })).toHaveStyle({
      minHeight: "calc(2.75rem * var(--mantine-scale))",
    });
    expect(
      screen.getByRole("button", { name: "Try marking all notifications read again" }),
    ).toHaveStyle({ minHeight: "calc(2.75rem * var(--mantine-scale))" });
    expect(screen.getByRole("button", { name: "Dismiss mark-all error" })).toHaveStyle({
      minHeight: "calc(2.75rem * var(--mantine-scale))",
    });

    const retry = screen.getByRole("button", {
      name: "Try marking all notifications read again",
    });
    await user.click(retry);
    await waitFor(() => expect(requests).toBe(2));
    expect(alert).toBeInTheDocument();
    expect(retry).toBeDisabled();

    await user.click(retry);
    expect(requests).toBe(2);

    await act(async () => finishRetry?.(HttpResponse.json({ marked: 2 })));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("does not resurrect a dismissed page mark-all failure after its retry settles", async () => {
    const user = userEvent.setup();
    let requests = 0;
    let finishRetry: ((response: Response) => void) | undefined;
    server.use(
      http.post("/api/v1/notifications/read-all", () => {
        requests += 1;
        if (requests === 1) {
          return HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 });
        }
        if (requests === 2) {
          return new Promise<Response>((resolve) => {
            finishRetry = resolve;
          });
        }
        return HttpResponse.json({ marked: 2 });
      }),
    );

    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    const markAll = await screen.findByRole("button", { name: "Mark all read" });
    await user.click(markAll);
    await user.click(
      await screen.findByRole("button", { name: "Try marking all notifications read again" }),
    );
    await waitFor(() => expect(requests).toBe(2));

    await user.click(screen.getByRole("button", { name: "Dismiss mark-all error" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(markAll).toBeDisabled();
    await user.click(markAll);
    expect(requests).toBe(2);

    await act(async () =>
      finishRetry?.(HttpResponse.json({ detail: "still unavailable" }, { status: 503 })),
    );
    await waitFor(() => expect(markAll).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
