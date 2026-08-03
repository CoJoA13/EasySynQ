import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser, ProvisionedUser, RoleSummary } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { CreateUserModal } from "./CreateUserModal";

// Pinned to the real _represent(...) shape (api/users.py) via `satisfies AdminUser`.
const PROVISIONED_USER = {
  id: "us000010-0010-0010-0010-000000000010",
  keycloak_subject: "kc-newhire",
  display_name: "New Hire",
  email: "newhire@example.com",
  status: "INVITED",
  mfa_enrolled: false,
  is_guest: false,
  roles: [],
} satisfies AdminUser;

// Pinned to POST /api/v1/users/provision's 201 body (api/users.py::provision_user) via `satisfies`.
const PROVISION_RESPONSE = {
  user: PROVISIONED_USER,
  temporary_password: "Xk4-Marmot-Bridge-77",
  password_delivery: "shown_once",
} satisfies ProvisionedUser;

const COLLISION_SUBJECT = "kc-existing-orphan-42";

// Pinned to _role (api/authz.py) via `satisfies`.
const ROLES = [
  {
    id: "ro000001-0001-0001-0001-000000000001",
    name: "Employee",
    description: null,
    is_reserved: false,
  },
  {
    id: "ro000002-0002-0002-0002-000000000002",
    name: "Quality Manager",
    description: null,
    is_reserved: true,
  },
] satisfies RoleSummary[];

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

function renderModal(onClose: () => void = () => {}) {
  return renderWithProviders(<CreateUserModal opened onClose={onClose} token="test-token" />);
}

describe("CreateUserModal", () => {
  it("submits the typed username to POST /users/provision and renders the show-once password", async () => {
    let body: unknown;
    server.use(
      http.post("/api/v1/users/provision", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(PROVISION_RESPONSE, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderModal();

    // Mantine v7 injects an aria-hidden " *" into a required-field label's textContent (the
    // ImplementCreateDcrModal precedent) — match by regex, not the exact "Username".
    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByText(PROVISION_RESPONSE.temporary_password)).toBeInTheDocument();
    expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeInTheDocument();
    expect(body).toMatchObject({ username: "newhire" });
  });

  it("a 409 keycloak_username_exists_unlinked offers Link the existing account, posting the collision subject to POST /users", async () => {
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(
          {
            code: "keycloak_username_exists_unlinked",
            title: "A sign-in account with that username already exists",
            detail: "Link the existing account instead of creating a new one.",
            keycloak_subject: COLLISION_SUBJECT,
          },
          { status: 409 },
        ),
      ),
    );
    let linkBody: unknown;
    server.use(
      http.post("/api/v1/users", async ({ request }) => {
        linkBody = await request.json();
        return HttpResponse.json(
          {
            id: "us000011-0011-0011-0011-000000000011",
            keycloak_subject: COLLISION_SUBJECT,
            display_name: "orphan",
            email: null,
            status: "INVITED",
            mfa_enrolled: false,
            is_guest: false,
            roles: [],
          } satisfies AdminUser,
          { status: 201 },
        );
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderModal(onClose);

    await user.type(screen.getByLabelText(/Username/), "orphan");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(
      await screen.findByText(/sign-in account with that username already exists/),
    ).toBeInTheDocument();
    const linkButton = screen.getByRole("button", { name: "Link the existing account" });
    expect(screen.getByRole("button", { name: "Choose a different username" })).toBeInTheDocument();

    await user.click(linkButton);

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(linkBody).toMatchObject({ keycloak_subject: COLLISION_SUBJECT });
  });

  it("Choose a different username returns to the editable form without closing the modal", async () => {
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(
          {
            code: "keycloak_username_exists_unlinked",
            title: "A sign-in account with that username already exists",
            keycloak_subject: COLLISION_SUBJECT,
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText(/Username/), "orphan");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    await screen.findByRole("button", { name: "Link the existing account" });

    await user.click(screen.getByRole("button", { name: "Choose a different username" }));

    expect(await screen.findByLabelText(/Username/)).toHaveValue("orphan");
    expect(screen.queryByRole("button", { name: "Link the existing account" })).toBeNull();
  });

  it("a 409 keycloak_email_exists surfaces inline against the Email field", async () => {
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(
          {
            code: "keycloak_email_exists",
            title: "That email is already used by another sign-in account",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText(/Username/), "dupe");
    await user.type(screen.getByLabelText("Email"), "dupe@example.com");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByText(/already used by another sign-in account/)).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    // Distinct from the generic top-of-form banner — this is a field-scoped error, not a fallback.
    expect(screen.queryByText("Couldn't create user")).toBeNull();
  });

  it("the role picker is absent without permission.grant", async () => {
    renderModal();
    await screen.findByLabelText(/Username/);
    expect(screen.queryByText("Roles")).toBeNull();
  });

  it("the role picker is present with permission.grant and lists the seeded roles", async () => {
    grant(["permission.grant"]);
    server.use(http.get("/api/v1/roles", () => HttpResponse.json(ROLES)));
    const user = userEvent.setup();
    renderModal();

    expect(await screen.findByText("Roles")).toBeInTheDocument();
    await user.click(screen.getByPlaceholderText(/assign roles/i));
    expect(await screen.findByText("Employee")).toBeInTheDocument();
    expect(screen.getByText("Quality Manager")).toBeInTheDocument();
  });

  it("has no axe violations (Mantine portals the modal onto document.body)", async () => {
    // A custom queryClient lets the test wait for the background usePermissions() fetch to fully
    // settle (the LeftRail no-clause-links precedent) before auditing — otherwise its resolution
    // lands after the assertion and React warns about an unwrapped act() state update.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithProviders(<CreateUserModal opened onClose={() => {}} token="test-token" />, {
      queryClient,
    });
    await screen.findByLabelText(/Username/);
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    // The Modal is portalled outside Testing Library's render container.
    expect(await axe(document.body)).toHaveNoViolations();
  });
});
