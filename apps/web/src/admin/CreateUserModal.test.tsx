import { QueryClient, useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { apiGet } from "../lib/api";
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

// Named (not ROLES[0].id) so a `role_ids` assertion never trips noUncheckedIndexedAccess.
const EMPLOYEE_ROLE_ID = "ro000001-0001-0001-0001-000000000001";

// Pinned to _role (api/authz.py) via `satisfies`.
const ROLES = [
  {
    id: EMPLOYEE_ROLE_ID,
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
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(PROVISION_RESPONSE.temporary_password)).toBeInTheDocument();
    expect(screen.getByText(/cannot be shown again/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeInTheDocument();
    expect(body).toMatchObject({ username: "newhire" });
  });

  it("clears the issued temporary password and form when the modal is closed and reopened (same instance, not a fresh mount)", async () => {
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(PROVISION_RESPONSE, { status: 201 }),
      ),
    );
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { rerender } = renderModal(onClose);

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(PROVISION_RESPONSE.temporary_password)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();

    // Toggle `opened` on the SAME component instance via rerender — never unmount + freshly mount,
    // since a brand-new instance would pass this assertion even with the reset bug (useState always
    // starts blank on a fresh mount, masking a missing reset in close()).
    rerender(<CreateUserModal opened={false} onClose={onClose} token="test-token" />);
    rerender(<CreateUserModal opened onClose={onClose} token="test-token" />);

    // The plain form only renders once BOTH `issued` and `collision` are falsy — if close() stops
    // resetting them, this never resolves and the test fails here (loudly), not silently.
    await screen.findByLabelText(/Username/);
    expect(screen.queryByText(PROVISION_RESPONSE.temporary_password)).toBeNull();
    expect(screen.getByLabelText(/Username/)).toHaveValue("");
  });

  it("does not retain the issued password in TanStack Query's mutation cache after issuing and closing", async () => {
    // Fix 2: the mutation cache keeps `state.data` (INCLUDING temporary_password) on its OWN
    // Mutation object, entirely separate from the `issued` React state cleared above — this
    // component never unmounts on close, so nothing but an explicit createMut.reset() scrubs it.
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(PROVISION_RESPONSE, { status: 201 }),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithProviders(<CreateUserModal opened onClose={onClose} token="test-token" />, {
      queryClient,
    });

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(PROVISION_RESPONSE.temporary_password)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();

    await waitFor(() => {
      const stillCached = queryClient
        .getMutationCache()
        .getAll()
        .some((m) =>
          JSON.stringify(m.state.data ?? null).includes(PROVISION_RESPONSE.temporary_password),
        );
      expect(stillCached).toBe(false);
    });
  });

  it("refuses to close while the account is being created, then closes normally once the password is shown", async () => {
    // The same hazard as ManageUser's reissue (P1, PR #429 review): this component never unmounts
    // on close (UsersAdmin renders it unconditionally, gated only on `opened`), so onSuccess would
    // still land even if the operator closed early — but by then `opened` would already be false,
    // so the ONE moment the password is shown never happens on screen, and the very next open would
    // show it out of context against a blank form for what looks like a brand-new user. An MSW
    // handler that does not resolve until `release` is called reproduces that in-flight window.
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/users/provision", async () => {
        await gate;
        return HttpResponse.json(PROVISION_RESPONSE, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal(onClose);

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create" }));

    // In flight: Cancel is disabled, the close (X) control is gone, and the panel explains why.
    expect(await screen.findByText(/stays open until the password is shown/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();

    // Escape must not close it either — closeOnEscape is false for the whole in-flight window.
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // Release the response: the password is shown, and ONLY NOW does the modal become closable.
    act(() => release?.());
    expect(await screen.findByText(PROVISION_RESPONSE.temporary_password)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("a failed account creation leaves the modal closable, not wedged open", async () => {
    // The busy flag is set as soon as createMut starts and must be cleared on BOTH outcomes (it
    // rides createMut.isPending directly, which TanStack Query clears on settle regardless of
    // success/failure) — or a failed create would wedge the modal open forever.
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/users/provision", async () => {
        await gate;
        return HttpResponse.json(
          { code: "keycloak_unavailable", title: "Could not reach Keycloak" },
          { status: 502 },
        );
      }),
    );
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal(onClose);

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create" }));

    // Confirm it is genuinely blocked first — proving the reset below is a real transition, not a
    // vacuous pass because the guard never engaged.
    expect(await screen.findByText(/stays open until the password is shown/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();

    act(() => release?.());

    // The failed mutation settled — the modal is closable again, proving the flag cannot wedge it.
    expect(await screen.findByText("Couldn't create user")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("a 502 keycloak_unavailable (the post-commit credential failure) still refreshes the roster, while still showing the error", async () => {
    // Fix 1: the API sets the Keycloak password only AFTER the app_user row commits, so a failure
    // here leaves a real, credential-less user in the roster that the error message tells the
    // operator to repair via reissue — but only onSuccess invalidated ["users"], so that user
    // stayed invisible without a manual reload. Mount a probe query on the SAME queryClient
    // (mirroring how UsersAdmin actually shares one with this modal in production) so an
    // invalidation here has an active observer to refetch, and count real GET requests rather than
    // just asserting a method was called.
    let usersRequests = 0;
    server.use(
      http.get("/api/v1/users", () => {
        usersRequests += 1;
        return HttpResponse.json([]);
      }),
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(
          {
            code: "keycloak_unavailable",
            title: "User created but the temporary password could not be set",
            detail:
              "The Keycloak account and EasySynQ user were created, but Keycloak could not be " +
              "reached to set the temporary password. Do not retry create — reissue a temporary " +
              "password for this user instead.",
          },
          { status: 502 },
        ),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    function RosterProbe() {
      useQuery({
        queryKey: ["users"],
        queryFn: () => apiGet<AdminUser[]>("/api/v1/users", "test-token"),
      });
      return null;
    }
    renderWithProviders(
      <>
        <RosterProbe />
        <CreateUserModal opened onClose={() => {}} token="test-token" />
      </>,
      { queryClient },
    );
    await waitFor(() => expect(usersRequests).toBe(1));

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("Couldn't create user")).toBeInTheDocument();
    expect(
      screen.getByText(/reissue a temporary password for this user instead/),
    ).toBeInTheDocument();
    // The roster query was invalidated and, since it has an active observer, actually refetched —
    // not just marked stale for a refetch that never comes.
    await waitFor(() => expect(usersRequests).toBe(2));
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
    await user.click(screen.getByRole("button", { name: "Create" }));

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
    await user.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByRole("button", { name: "Link the existing account" });

    await user.click(screen.getByRole("button", { name: "Choose a different username" }));

    expect(await screen.findByLabelText(/Username/)).toHaveValue("orphan");
    expect(screen.queryByRole("button", { name: "Link the existing account" })).toBeNull();
  });

  it("a malformed 409 (no usable keycloak_subject) surfaces an error instead of a blank form", async () => {
    server.use(
      http.post("/api/v1/users/provision", () =>
        HttpResponse.json(
          {
            code: "keycloak_username_exists_unlinked",
            title: "A sign-in account with that username already exists",
            // No keycloak_subject at all — the real backend always sets it for this code, but the
            // client must not silently blank the form on a malformed body.
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText(/Username/), "orphan");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("Couldn't create user")).toBeInTheDocument();
    expect(
      screen.getByText("A sign-in account with that username already exists"),
    ).toBeInTheDocument();
    // The generic error path, not the collision Alert — the editable form must still be present.
    expect(screen.queryByRole("button", { name: "Link the existing account" })).toBeNull();
    expect(screen.getByLabelText(/Username/)).toHaveValue("orphan");
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
    await user.click(screen.getByRole("button", { name: "Create" }));

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

  it("selecting a role reaches role_ids in the POST body, alongside other optional fields", async () => {
    grant(["permission.grant"]);
    server.use(http.get("/api/v1/roles", () => HttpResponse.json(ROLES)));
    let body: unknown;
    server.use(
      http.post("/api/v1/users/provision", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(PROVISION_RESPONSE, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText(/Username/), "newhire");
    await user.type(screen.getByLabelText("Display name"), "New Hire");
    await screen.findByText("Roles");
    await user.click(screen.getByPlaceholderText(/assign roles/i));
    await user.click(await screen.findByText("Employee"));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByText(PROVISION_RESPONSE.temporary_password);
    expect(body).toMatchObject({
      username: "newhire",
      display_name: "New Hire",
      role_ids: [EMPLOYEE_ROLE_ID],
    });
  });

  it("the collision Alert warns that a selected role will not be assigned by linking", async () => {
    grant(["permission.grant"]);
    server.use(http.get("/api/v1/roles", () => HttpResponse.json(ROLES)));
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
    await screen.findByText("Roles");
    await user.click(screen.getByPlaceholderText(/assign roles/i));
    await user.click(await screen.findByText("Employee"));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByRole("button", { name: "Link the existing account" });
    // linkMut must still send only the fields it always sent — the warning is informational,
    // not a behavior change.
    expect(screen.getByText(/will not be assigned by linking/)).toBeInTheDocument();
  });

  it("the collision Alert has no role-drop warning when no role was selected", async () => {
    grant(["permission.grant"]);
    server.use(http.get("/api/v1/roles", () => HttpResponse.json(ROLES)));
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

    // The role picker is available (permission.grant is held, roles are loaded) but nothing is
    // picked — the warning must not appear just because the caller COULD select roles.
    await user.type(screen.getByLabelText(/Username/), "orphan");
    await screen.findByText("Roles");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByRole("button", { name: "Link the existing account" });
    expect(screen.queryByText(/will not be assigned by linking/)).toBeNull();
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
