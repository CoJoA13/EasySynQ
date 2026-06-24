import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { OrgConfig } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { orgConfigFixture } from "../test/msw/handlers";
import { ConfigAdmin } from "./ConfigAdmin";

function statefulConfig(initial: OrgConfig = orgConfigFixture, onPatch?: (b: unknown) => void) {
  let current: OrgConfig = { ...initial };
  return [
    http.get("/api/v1/admin/config", () =>
      HttpResponse.json(current as unknown as Record<string, unknown>),
    ),
    http.patch("/api/v1/admin/config", async ({ request }) => {
      const b = (await request.json()) as Partial<OrgConfig>;
      onPatch?.(b);
      current = { ...current, ...b };
      return HttpResponse.json(current as unknown as Record<string, unknown>);
    }),
  ];
}

describe("ConfigAdmin", () => {
  it("reflects the loaded config and is accessible", async () => {
    server.use(...statefulConfig({ ...orgConfigFixture, notifications_email_enabled: false }));
    const { container } = renderWithProviders(<ConfigAdmin />);
    expect(
      await screen.findByRole("switch", { name: "Email delivery (organisation-wide)" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("switch", { name: "Escalation pierces quiet hours" }),
    ).toBeChecked();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("saves a changed toggle as a partial PATCH and stays", async () => {
    let body: unknown = null;
    server.use(
      ...statefulConfig(orgConfigFixture, (b) => {
        body = b;
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ConfigAdmin />);
    const sw = await screen.findByRole("switch", { name: "Email delivery (organisation-wide)" });
    expect(sw).toBeChecked();
    await user.click(sw);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(body).toEqual({ notifications_email_enabled: false }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Email delivery (organisation-wide)" }),
    ).not.toBeChecked();
  });

  it("shows a no-access panel when the config read is forbidden (no permissions probe)", async () => {
    server.use(
      http.get("/api/v1/admin/config", () =>
        HttpResponse.json({ code: "forbidden", title: "no" }, { status: 403 }),
      ),
    );
    renderWithProviders(<ConfigAdmin />);
    expect(await screen.findByText(/You need config\.update/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("shows a retryable error (not a spinner) when the config load fails", async () => {
    server.use(
      http.get("/api/v1/admin/config", () =>
        HttpResponse.json({ code: "boom", title: "nope" }, { status: 500 }),
      ),
    );
    renderWithProviders(<ConfigAdmin />);
    expect(await screen.findByText("Couldn't load configuration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
