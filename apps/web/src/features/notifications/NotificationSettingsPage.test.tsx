import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { NotificationPreferences } from "../../lib/types";
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
} satisfies NotificationPreferences;

function getPrefs(overrides: Record<string, unknown> = {}) {
  return http.get("/api/v1/me/notification-preferences", () =>
    HttpResponse.json({ ...FULL_PREFS, ...overrides } as Record<string, unknown>),
  );
}

function statefulPrefs(
  initial: NotificationPreferences = FULL_PREFS,
  onPut?: (body: unknown) => void,
) {
  let current: NotificationPreferences = { ...initial, digest_modes: { ...initial.digest_modes } };
  return [
    http.get("/api/v1/me/notification-preferences", () =>
      HttpResponse.json(current as unknown as Record<string, unknown>),
    ),
    http.put("/api/v1/me/notification-preferences", async ({ request }) => {
      const b = (await request.json()) as Partial<NotificationPreferences>;
      onPut?.(b);
      current = {
        ...current,
        ...b,
        digest_modes: { ...current.digest_modes, ...(b.digest_modes ?? {}) },
      };
      return HttpResponse.json(current as unknown as Record<string, unknown>);
    }),
  ];
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
      ...statefulPrefs(FULL_PREFS, (b) => {
        body = b;
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
    // stateful: refetch returns action_required:"off" so the control stays checked
    expect(within(group).getByRole("radio", { name: "Off" })).toBeChecked();
  });

  it("saves the master email toggle as a partial PUT", async () => {
    let body: unknown = null;
    server.use(
      ...statefulPrefs(FULL_PREFS, (b) => {
        body = b;
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

describe("NotificationSettingsPage — daily digest timing", () => {
  it("saves a changed digest hour", async () => {
    let body: unknown = null;
    server.use(
      ...statefulPrefs(FULL_PREFS, (b) => {
        body = b;
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByLabelText("Send the daily digest at"));
    await user.click(await screen.findByText("06:00"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ digest_hour: 6 }));
  });

  it("searches and saves a non-curated timezone", async () => {
    let body: unknown = null;
    server.use(
      ...statefulPrefs(FULL_PREFS, (b) => {
        body = b;
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    const tz = await screen.findByLabelText("Timezone");
    await user.click(tz);
    await user.clear(tz);
    await user.type(tz, "Anchorage");
    await user.click(await screen.findByText("America/Anchorage"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ timezone: "America/Anchorage" }));
  });

  it("offers the curated common zones at rest (before typing)", async () => {
    server.use(getPrefs()); // timezone defaults to "UTC"
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByLabelText("Timezone"));
    // curated zones are offered without any typing
    expect(await screen.findByText("Europe/London")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
  });

  it("enabling quiet hours saves both bounds together", async () => {
    let body: unknown = null;
    server.use(
      ...statefulPrefs(FULL_PREFS, (b) => {
        body = b;
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByRole("switch", { name: "Enable quiet hours" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ quiet_start: "22:00", quiet_end: "07:00" }));
  });

  it("disabling quiet hours clears both bounds", async () => {
    let body: unknown = null;
    server.use(
      ...statefulPrefs({ ...FULL_PREFS, quiet_start: "22:00", quiet_end: "07:00" }, (b) => {
        body = b;
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    await user.click(await screen.findByRole("switch", { name: "Enable quiet hours" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ quiet_start: null, quiet_end: null }));
  });

  it("warns when the digest hour falls inside quiet hours", async () => {
    server.use(getPrefs({ digest_hour: 23, quiet_start: "22:00", quiet_end: "07:00" }));
    renderWithProviders(<NotificationSettingsPage />, { route: "/settings/notifications" });
    expect(await screen.findByText(/digest hour is within your quiet hours/i)).toBeInTheDocument();
  });
});
