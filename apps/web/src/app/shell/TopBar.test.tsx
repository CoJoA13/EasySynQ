// apps/web/src/app/shell/TopBar.test.tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { TopBar } from "./TopBar";

function renderBar() {
  return renderWithProviders(
    <TopBar navOpened={false} onToggleNav={() => {}} onOpenSearch={() => {}} />,
    { route: "/" },
  );
}

describe("TopBar", () => {
  test("keeps the Tasks work entry with a distinct label", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderBar();
    const tasks = await screen.findByRole("link", { name: "Tasks" });
    expect(tasks).toHaveAttribute("href", "/tasks");
  });

  test("renders the merged notification bell with an unread badge", async () => {
    server.use(
      http.get("/api/v1/notifications", () =>
        HttpResponse.json([
          {
            id: "n1",
            event_key: "task.assigned",
            subject_type: "DOCUMENT",
            subject_id: "d1",
            title: "Review requested",
            body: "",
            deep_link: "http://localhost/documents/d1",
            created_at: "2026-06-22T09:00:00Z",
            read_at: null,
          },
        ]),
      ),
    );
    renderBar();
    expect(await screen.findByText("1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications, 1 unread" })).toBeInTheDocument();
  });

  test("the account menu offers notification settings", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    renderBar();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Account" }));
    expect(await screen.findByRole("menuitem", { name: "Notification settings" })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });

  it("shows an Administration link when the caller has config.update", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: [{ key: "config.update", effect: "ALLOW" }],
        }),
      ),
    );
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByRole("button", { name: "Account" }));
    expect(await screen.findByRole("menuitem", { name: "Administration" })).toHaveAttribute(
      "href",
      "/admin/config",
    );
  });

  it("hides the Administration link without config.update", async () => {
    server.use(http.get("/api/v1/notifications", () => HttpResponse.json([])));
    // default /me/permissions handler returns an empty permission set
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByRole("button", { name: "Account" }));
    // await a stable sibling so the menu is open + the permissions query has settled before asserting absence
    expect(await screen.findByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Administration" })).not.toBeInTheDocument();
  });
});
