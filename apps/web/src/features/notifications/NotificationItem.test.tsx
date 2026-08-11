// apps/web/src/features/notifications/NotificationItem.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { Notification } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationItem } from "./NotificationItem";

const unread: Notification = {
  id: "n1",
  event_key: "task.assigned",
  subject_type: "DOCUMENT",
  subject_id: "d1",
  title: "Review requested: SOP-001",
  body: "You have a review task.",
  deep_link: "http://localhost/documents/d1",
  created_at: "2026-06-22T09:00:00Z",
  read_at: null,
};

describe("NotificationItem", () => {
  it("marks an unread row with the dot+label and a bold title, and links to the relative path", () => {
    renderWithProviders(<NotificationItem notification={unread} />);
    expect(screen.getByText("Unread")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review requested: SOP-001/ })).toHaveAttribute(
      "href",
      "/documents/d1",
    );
    expect(screen.getByLabelText("Mark read: Review requested: SOP-001")).toBeInTheDocument();
  });

  it("a read row has no unread marker and no mark-read button", () => {
    renderWithProviders(
      <NotificationItem notification={{ ...unread, read_at: "2026-06-22T10:00:00Z" }} />,
    );
    expect(screen.queryByText("Unread")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Mark read:/)).not.toBeInTheDocument();
  });

  it("renders the body as literal text (no HTML injection)", () => {
    renderWithProviders(
      <NotificationItem notification={{ ...unread, body: "<b>x</b><script>alert(1)</script>" }} />,
    );
    expect(screen.getByText("<b>x</b><script>alert(1)</script>")).toBeInTheDocument();
  });

  it("the mark-read button POSTs the id without navigating", async () => {
    let marked = "";
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        marked = String(params.id);
        return HttpResponse.json({ status: "ok" });
      }),
    );
    renderWithProviders(<NotificationItem notification={unread} />);
    await userEvent.click(screen.getByLabelText("Mark read: Review requested: SOP-001"));
    await waitFor(() => expect(marked).toBe("n1"));
  });

  it("keeps a failed explicit mark-read local and retries the same notification", async () => {
    const user = userEvent.setup();
    const requestedIds: string[] = [];
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        requestedIds.push(String(params.id));
        return requestedIds.length === 1
          ? HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 })
          : HttpResponse.json({ status: "ok" });
      }),
    );

    renderWithProviders(<NotificationItem notification={unread} />);
    await user.click(screen.getByLabelText("Mark read: Review requested: SOP-001"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't mark this notification read",
    );
    expect(screen.getByText("Unread")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "Try marking this notification read again",
      }),
    );
    await waitFor(() => expect(requestedIds).toEqual(["n1", "n1"]));
  });

  it("offers Dismiss but no Retry after a non-retryable explicit mark-read failure", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/notifications/:id/read", () =>
        HttpResponse.json({ detail: "notification not found" }, { status: 404 }),
      ),
    );

    renderWithProviders(<NotificationItem notification={unread} />);
    await user.click(screen.getByLabelText("Mark read: Review requested: SOP-001"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't mark this notification read",
    );
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try marking this notification read again" }),
    ).not.toBeInTheDocument();
  });

  it("gives the explicit mark-read action a 44px minimum target", () => {
    renderWithProviders(<NotificationItem notification={unread} />);

    expect(screen.getByLabelText("Mark read: Review requested: SOP-001")).toHaveStyle({
      minWidth: "44px",
      minHeight: "44px",
    });
  });

  it("clicking the row marks read and fires onNavigate", async () => {
    let marked = "";
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        marked = String(params.id);
        return HttpResponse.json({ status: "ok" });
      }),
    );
    const onNavigate = vi.fn();
    renderWithProviders(<NotificationItem notification={unread} onNavigate={onNavigate} />);
    await userEvent.click(screen.getByRole("link", { name: /Review requested: SOP-001/ }));
    await waitFor(() => expect(marked).toBe("n1"));
    expect(onNavigate).toHaveBeenCalled();
  });
});
