import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { TONE_GLYPH } from "../lib/status";
import type { NotificationDeliveryHealth } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { notificationHealthFixture } from "../test/msw/handlers";
import { NotificationHealthPanel } from "./NotificationHealthPanel";

function health(over: Partial<NotificationDeliveryHealth>) {
  server.use(
    http.get("/api/v1/admin/notifications/health", () =>
      HttpResponse.json({ ...notificationHealthFixture, ...over } as unknown as Record<
        string,
        unknown
      >),
    ),
  );
}

describe("NotificationHealthPanel", () => {
  it("renders failures with the danger glyph when failed > 0, and is accessible", async () => {
    health({ email: { ...notificationHealthFixture.email, failed: 3 } });
    const { container } = renderWithProviders(<NotificationHealthPanel />);
    const failed = await screen.findByLabelText("Email delivery failures: 3");
    expect(failed).toHaveTextContent(TONE_GLYPH.danger);
    expect(screen.getByText("ops@example.com")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows no danger glyph when failed is 0 (negative)", async () => {
    health({ email: { ...notificationHealthFixture.email, failed: 0 }, recent_failures: [] });
    renderWithProviders(<NotificationHealthPanel />);
    const failed = await screen.findByLabelText("Email delivery failures: 0");
    expect(failed).not.toHaveTextContent(TONE_GLYPH.danger);
    expect(screen.queryByText(TONE_GLYPH.danger)).not.toBeInTheDocument();
    expect(screen.getByText("No delivery failures.")).toBeInTheDocument();
  });

  it("shows the email-off banner when delivery is disabled org-wide", async () => {
    health({ org_email_enabled: false });
    renderWithProviders(<NotificationHealthPanel />);
    expect(await screen.findByText("Email delivery is off")).toBeInTheDocument();
  });

  it("shows a retryable error (not a spinner) when the health load fails", async () => {
    server.use(
      http.get("/api/v1/admin/notifications/health", () =>
        HttpResponse.json({ code: "boom", title: "nope" }, { status: 500 }),
      ),
    );
    renderWithProviders(<NotificationHealthPanel />);
    expect(await screen.findByText("Couldn't load delivery health")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows the oldest-pending-email line and awareness backlog count when pending > 0", async () => {
    health({
      email: {
        ...notificationHealthFixture.email,
        pending_now: 2,
        pending_scheduled: 0,
        oldest_pending_at: "2026-06-24T08:00:00Z",
      },
      awareness: { pending: 4, oldest_pending_at: "2026-06-24T07:00:00Z" },
    });
    renderWithProviders(<NotificationHealthPanel />);
    expect(await screen.findByText(/Oldest pending email/)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(await screen.findByText(/Oldest pending awareness event/)).toBeInTheDocument();
  });
});
