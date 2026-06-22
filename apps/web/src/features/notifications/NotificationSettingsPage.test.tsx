// apps/web/src/features/notifications/NotificationSettingsPage.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationSettingsPage } from "./NotificationSettingsPage";

describe("NotificationSettingsPage", () => {
  it("reflects the current email_enabled value", async () => {
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    const sw = await screen.findByRole("switch", { name: "Email notifications" });
    expect(sw).not.toBeChecked();
  });

  it("PUTs the new value when toggled and confirms the save", async () => {
    let body: unknown = null;
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body);
      }),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await userEvent.click(await screen.findByRole("switch", { name: "Email notifications" }));
    await waitFor(() => expect(body).toEqual({ email_enabled: true }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("surfaces a save error", async () => {
    server.use(
      http.get("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ email_enabled: false }),
      ),
      http.put("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ code: "boom", title: "Save failed" }, { status: 500 }),
      ),
    );
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await userEvent.click(await screen.findByRole("switch", { name: "Email notifications" }));
    expect(await screen.findByText("Couldn't save your preference")).toBeInTheDocument();
  });
});
