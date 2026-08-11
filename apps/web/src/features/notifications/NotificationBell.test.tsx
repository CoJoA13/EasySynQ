// apps/web/src/features/notifications/NotificationBell.test.tsx
import { act, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";

const { openSpy } = vi.hoisted(() => ({
  openSpy: vi.fn(() => new Promise<void>(() => {})), // stay open, no reconnect churn
}));
vi.mock("./stream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./stream")>()),
  openNotificationStream: openSpy,
}));

import { NotificationBell } from "./NotificationBell";

function unreadList(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `u${i}`,
    event_key: "task.assigned",
    subject_type: "DOCUMENT",
    subject_id: `d${i}`,
    title: `Notice ${i}`,
    body: "",
    deep_link: `http://localhost/documents/d${i}`,
    created_at: "2026-06-22T09:00:00Z",
    read_at: null,
  }));
}

describe("NotificationBell", () => {
  it("shows the unread count and names itself with it", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(3))));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications, 3 unread" })).toBeInTheDocument();
  });

  it("caps the badge at 99+", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(100))));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByText("99+")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications, 100 unread" })).toBeInTheDocument();
  });

  it("a failed count shows an indeterminate bell — never a confident 0", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    renderWithProviders(<NotificationBell />);
    expect(
      await screen.findByRole("button", { name: "Notifications (count unavailable)" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("a genuine zero is silent", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("opens the popover with the recent list, settings and see-all links", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))));
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("Notice 0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "/notifications");
    expect(screen.getByRole("link", { name: "Notification settings" })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });

  it("moves keyboard focus into the popover and returns it to the bell on close", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(1))));
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <NotificationBell />
        <button>Account menu</button>
      </>,
    );
    const bell = await screen.findByRole("button", { name: /Notifications/ });
    bell.focus();
    await user.keyboard("{Enter}");

    const popover = await screen.findByRole("dialog");
    await waitFor(() => expect(popover).toContainElement(document.activeElement as HTMLElement));
    await user.tab();
    expect(screen.getByRole("button", { name: "Account menu" })).not.toHaveFocus();
    expect(popover).toContainElement(document.activeElement as HTMLElement);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(bell).toHaveFocus());
  });

  it("mark all read POSTs read-all", async () => {
    let hit = false;
    server.use(
      http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))),
      http.post("/api/v1/notifications/read-all", () => {
        hit = true;
        return HttpResponse.json({ marked: 2 });
      }),
    );
    renderWithProviders(<NotificationBell />);
    await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(hit).toBe(true));
  });

  it("keeps a failed mark-all read in the bell and retries the same operation", async () => {
    const user = userEvent.setup();
    let requests = 0;
    let finishRetry: ((response: Response) => void) | undefined;
    server.use(
      http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))),
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

    const { container } = renderWithProviders(<NotificationBell />);
    await user.click(await screen.findByRole("button", { name: /Notifications/ }));
    await user.click(await screen.findByRole("button", { name: "Mark all read" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't mark notifications read");
    expect(alert.closest('[role="dialog"]')).toBeInTheDocument();
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

  it("shows safe server copy and Dismiss without Retry for a forbidden bell mark-all", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/v1/notifications", () => HttpResponse.json(unreadList(2))),
      http.post("/api/v1/notifications/read-all", () =>
        HttpResponse.json(
          { detail: "You don't have permission to mark notifications read." },
          { status: 403 },
        ),
      ),
    );

    renderWithProviders(<NotificationBell />);
    await user.click(await screen.findByRole("button", { name: /Notifications/ }));
    await user.click(await screen.findByRole("button", { name: "Mark all read" }));

    expect(
      await screen.findByText("You don't have permission to mark notifications read."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss mark-all error" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try marking all notifications read again" }),
    ).not.toBeInTheDocument();
  });

  it("renders the bell and mounts the notification stream", async () => {
    openSpy.mockClear();
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByRole("button", { name: /notifications/i })).toBeInTheDocument();
    expect(openSpy).toHaveBeenCalledTimes(1);
  });
});
