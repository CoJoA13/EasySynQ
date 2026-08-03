import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import type { AdminUser, IssuedTemporaryPassword } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { UsersAdmin } from "./UsersAdmin";

const USER_ID = "us000001-0001-0001-0001-000000000001";
const USER = {
  id: USER_ID,
  keycloak_subject: "kc-mara",
  display_name: "Mara Quality",
  email: "mara@example.com",
  status: "ACTIVE",
  mfa_enrolled: true,
  is_guest: false,
  roles: [],
} satisfies AdminUser;

// Grants `user.create` — the same key the API gates .../temporary-password on — so the Manage
// drawer's "Issue new temp password" affordance renders (S-user-create Task 8 gates it client-side
// too, per DP-6: never offer a control the caller cannot exercise).
function grantUserCreate() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "user.create", effect: "ALLOW", source: null }],
      }),
    ),
  );
}

test("a manage action failure renders inside the open user drawer", async () => {
  server.use(
    http.get("/api/v1/users", () => HttpResponse.json([USER])),
    http.get("/api/v1/users/:id/roles", () => HttpResponse.json([])),
    http.get("/api/v1/users/:id/overrides", () => HttpResponse.json([])),
    http.post("/api/v1/users/:id/roles", () =>
      HttpResponse.json(
        {
          code: "role_conflict",
          title: "Role conflict",
          detail: "Role already assigned.",
        },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<UsersAdmin token="test-token" />);

  await user.click(await screen.findByRole("button", { name: "Manage" }));
  const drawer = await screen.findByRole("dialog");
  await user.click(await within(drawer).findByLabelText("Assign a role"));
  await user.click(await screen.findByRole("option", { name: "Employee" }));
  await user.click(within(drawer).getByRole("button", { name: "Assign" }));

  expect(await within(drawer).findByText("Action failed")).toBeInTheDocument();
  expect(within(drawer).getByText("role_conflict: Role already assigned.")).toBeInTheDocument();
});

test("revoke and remove controls name the specific assignment they affect", async () => {
  server.use(
    http.get("/api/v1/users", () => HttpResponse.json([USER])),
    http.get("/api/v1/users/:id/roles", () =>
      HttpResponse.json([
        { id: "assignment-1", role_name: "Employee" },
        { id: "assignment-2", role_name: "Process Owner" },
      ]),
    ),
    http.get("/api/v1/users/:id/overrides", () =>
      HttpResponse.json([
        {
          id: "override-1",
          permission_key: "document.read",
          effect: "ALLOW",
          scope: { level: "SYSTEM" },
        },
        {
          id: "override-2",
          permission_key: "document.release",
          effect: "DENY",
          scope: { level: "SYSTEM" },
        },
      ]),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<UsersAdmin token="test-token" />);

  await user.click(await screen.findByRole("button", { name: "Manage" }));
  const drawer = await screen.findByRole("dialog");

  expect(
    await within(drawer).findByRole("button", { name: "Revoke role Employee" }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole("button", { name: "Revoke role Process Owner" }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole("button", {
      name: "Remove allow system override for document.read",
    }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole("button", {
      name: "Remove deny system override for document.release",
    }),
  ).toBeInTheDocument();
});

test("the roster header offers Create user directly; the retired paste-a-subject Invite flow is gone", async () => {
  server.use(http.get("/api/v1/users", () => HttpResponse.json([USER])));
  // A dedicated queryClient lets the absence assertion wait for EVERY background fetch (the roster
  // list, plus CreateUserModal's always-mounted usePermissions() check) to settle first — otherwise
  // "Invite user" being absent could pass merely because nothing had rendered yet, not because the
  // button is truly gone (the repeated DOM-negative-guard trap in this codebase).
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithProviders(<UsersAdmin token="test-token" />, { queryClient });

  await screen.findByText(USER.display_name);
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));

  expect(screen.getByRole("button", { name: "Create user" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Invite user" })).toBeNull();
});

test("the Manage drawer's Issue new temp password action posts to the reissue endpoint for the right user and renders the show-once password", async () => {
  const NEW_PASSWORD = "Zq7-Falcon-Anchor-19";
  let capturedUrl: string | undefined;
  grantUserCreate();
  server.use(
    http.get("/api/v1/users", () => HttpResponse.json([USER])),
    http.get("/api/v1/users/:id/roles", () => HttpResponse.json([])),
    http.get("/api/v1/users/:id/overrides", () => HttpResponse.json([])),
    http.post("/api/v1/users/:id/temporary-password", ({ request }) => {
      capturedUrl = request.url;
      return HttpResponse.json(
        {
          temporary_password: NEW_PASSWORD,
          password_delivery: "shown_once",
        } satisfies IssuedTemporaryPassword,
        { status: 200 },
      );
    }),
  );
  const user = userEvent.setup();
  renderWithProviders(<UsersAdmin token="test-token" />);

  await user.click(await screen.findByRole("button", { name: "Manage" }));
  const drawer = await screen.findByRole("dialog");
  await user.click(await within(drawer).findByRole("button", { name: "Issue new temp password" }));

  expect(await within(drawer).findByText(NEW_PASSWORD)).toBeInTheDocument();
  expect(within(drawer).getByText(/cannot be shown again/)).toBeInTheDocument();
  expect(capturedUrl).toContain(`/api/v1/users/${USER_ID}/temporary-password`);
});
