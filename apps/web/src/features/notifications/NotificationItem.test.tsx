// apps/web/src/features/notifications/NotificationItem.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
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
});
