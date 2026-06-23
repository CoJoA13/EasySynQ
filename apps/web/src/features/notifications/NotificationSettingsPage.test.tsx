import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NotificationSettingsPage } from "./NotificationSettingsPage";

const FULL_PREFS = {
  email_enabled: true,
  digest_modes: {
    action_required: "daily",
    awareness: "daily",
    critical: "immediate",
    admin_ops: "immediate",
  },
  digest_hour: 8,
  timezone: "UTC",
  quiet_start: null,
  quiet_end: null,
};

function getPrefs(overrides: Record<string, unknown> = {}) {
  return http.get("/api/v1/me/notification-preferences", () =>
    HttpResponse.json({ ...FULL_PREFS, ...overrides } as Record<string, unknown>),
  );
}

describe("NotificationSettingsPage — cadence matrix", () => {
  it("reflects the loaded preferences and is accessible", async () => {
    server.use(
      getPrefs({
        email_enabled: false,
        digest_modes: { ...FULL_PREFS.digest_modes, action_required: "off" },
      }),
    );
    const { container } = renderWithProviders(<NotificationSettingsPage />, {
      route: "/settings/notifications",
    });
    // master toggle reflects email_enabled:false
    expect(await screen.findByRole("switch", { name: "Email notifications" })).not.toBeChecked();
    // the action_required cadence shows "Off" selected
    const group = await screen.findByRole("radiogroup", {
      name: "Email cadence — Things you must act on",
    });
    expect(within(group).getByRole("radio", { name: "Off" })).toBeChecked();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("saves a changed cadence as a partial PUT", async () => {
    let body: unknown = null;
    server.use(
      getPrefs(),
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body as Record<string, unknown>);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    const group = await screen.findByRole("radiogroup", {
      name: "Email cadence — Things you must act on",
    });
    await user.click(within(group).getByRole("radio", { name: "Off" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ digest_modes: { action_required: "off" } }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("saves the master email toggle as a partial PUT", async () => {
    let body: unknown = null;
    server.use(
      getPrefs(),
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body as Record<string, unknown>);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByRole("switch", { name: "Email notifications" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ email_enabled: false }));
  });

  it("surfaces a save error", async () => {
    server.use(
      getPrefs(),
      http.put("/api/v1/me/notification-preferences", () =>
        HttpResponse.json({ code: "boom", title: "Save failed" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByRole("switch", { name: "Email notifications" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Couldn't save your preferences")).toBeInTheDocument();
  });

  it("disables Save until something changes", async () => {
    server.use(getPrefs());
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    expect(await screen.findByRole("button", { name: "Save changes" })).toBeDisabled();
  });
});
