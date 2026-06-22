// apps/web/src/features/notifications/NotificationsPage.test.tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationsPage } from "./NotificationsPage";

describe("NotificationsPage", () => {
  it("lists notifications", async () => {
    renderWithProviders(<NotificationsPage />, { route: "/notifications" });
    expect(await screen.findByText("Review requested: SOP-001")).toBeInTheDocument();
    expect(screen.getByText("CAPA assigned: CAPA-002")).toBeInTheDocument();
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
});
