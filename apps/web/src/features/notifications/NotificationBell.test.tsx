// apps/web/src/features/notifications/NotificationBell.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
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
});
