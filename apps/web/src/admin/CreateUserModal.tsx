import { Alert, Button, Group, Modal, MultiSelect, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { usePermissions } from "../app/shell/usePermissions";
import { ApiError, apiGet, apiSend } from "../lib/api";
import type { AdminUser, ProvisionedUser, RoleSummary } from "../lib/types";
import { ShowOncePassword } from "./ShowOncePassword";

interface CreateUserFormState {
  username: string;
  display_name: string;
  email: string;
  first_name: string;
  last_name: string;
  role_ids: string[];
}

const EMPTY_FORM: CreateUserFormState = {
  username: "",
  display_name: "",
  email: "",
  first_name: "",
  last_name: "",
  role_ids: [],
};

// S-user-create Task 7: one form collapses "create a Keycloak account at the shell, then paste its
// sub into the Admin UI" into a single call — POST /users/provision creates the Keycloak account,
// the app_user row, and a generated temporary password in one transaction (api/users.py). Two 409s
// need distinct handling: an unlinked EXISTING Keycloak username offers "Link the existing account"
// (POST /users, the kept invite endpoint) rather than retrying create — EasySynQ never deletes a
// Keycloak account, so retrying would just collide again; an existing email surfaces inline against
// the Email field. The role picker only renders for a caller holding permission.grant — the API
// enforces this independently (assert_can_assign_role), but the UI must not offer a control the
// caller cannot exercise (DP-6).
export function CreateUserModal({
  opened,
  onClose,
  token,
}: {
  opened: boolean;
  onClose: () => void;
  token: string | null;
}) {
  const qc = useQueryClient();
  const canGrantRoles = usePermissions().can("permission.grant");

  const [form, setForm] = useState<CreateUserFormState>(EMPTY_FORM);
  const [collision, setCollision] = useState<string | null>(null);
  const [issued, setIssued] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);

  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: () => apiGet<RoleSummary[]>("/api/v1/roles", token),
    enabled: opened && canGrantRoles && !!token,
  });

  // Resets every piece of state that must never survive a reopen — most importantly `issued`: the
  // temporary password lives in this component's state ONLY (never localStorage/sessionStorage/a
  // URL/a log), so a stale value here would otherwise resurface the last operator's credential.
  //
  // Refuses to run at all while createMut is in flight (P1, PR #429 review — the same hazard as
  // ManageUser's reissue): this component never unmounts on close (UsersAdmin renders it
  // unconditionally, gated only on `opened`), so the eventual onSuccess/onError below would still
  // land — but by then `opened` would already be false and the operator gone, so the ONE moment
  // the password is shown never happens on screen, and the very next open would show it out of
  // context against a blank form for what looks like a brand-new user. Guarding here covers every
  // route: the Modal's own onClose (X / click-outside / Escape, see the props below) AND the
  // explicit Cancel button, which both call this same function.
  function close() {
    if (createMut.isPending) return;
    setIssued(null);
    setCollision(null);
    setError(null);
    setEmailError(null);
    setForm(EMPTY_FORM);
    onClose();
  }

  const createMut = useMutation({
    mutationFn: () => apiSend<ProvisionedUser>("POST", "/api/v1/users/provision", token, form),
    // TanStack Query's mutation cache retains the FULL response — including temporary_password —
    // as `state.data` until this mutation's observer detaches, and this component never unmounts
    // while the modal is merely closed (UsersAdmin renders it unconditionally, gated only on
    // `opened`). gcTime: 0 lets a detached mutation be dropped from the cache right away instead of
    // after the default 5-minute window; createMut.reset() (below) is what actually triggers that
    // detachment, called the instant the password is copied into `issued` — the one place the UI
    // reads it from — so the cache holds the credential for as little time as possible.
    gcTime: 0,
    onSuccess: (data) => {
      setIssued(data.temporary_password);
      createMut.reset();
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e: unknown) => {
      setError(null);
      setEmailError(null);
      // keycloak_unavailable is also returned for the POST-COMMIT failure: the app_user row
      // already committed and only the later Keycloak set-password call failed, so a real,
      // credential-less user now sits in the roster (api/users.py's detail tells the operator to
      // reissue a password, never to retry create). Invalidate unconditionally on this code so that
      // user becomes visible for the reissue action the error message advertises — the same code
      // also covers a PRE-commit failure where no row was ever created, where invalidating is just
      // a harmless no-op refetch, so a single check here is correct and needs no finer distinction.
      if (e instanceof ApiError && e.code === "keycloak_unavailable") {
        void qc.invalidateQueries({ queryKey: ["users"] });
      }
      // The collision subject comes off the RFC 9457 problem body, which ApiError already carries
      // as `problem` (members={"keycloak_subject": ...} in api/users.py::provision_user).
      if (e instanceof ApiError && e.code === "keycloak_username_exists_unlinked") {
        const subject = e.problem?.keycloak_subject;
        if (typeof subject === "string") {
          setCollision(subject);
          return;
        }
        // Malformed 409 — the code promises a collision subject but didn't include a usable string
        // one. Fall through to the generic error path below instead of leaving `collision` null,
        // which would silently render the blank form with no explanation for the failed submit.
      }
      if (e instanceof ApiError && e.code === "keycloak_email_exists") {
        setEmailError(e.message);
        return;
      }
      setError(e instanceof ApiError ? e.message : String(e));
    },
  });

  // The link path calls the KEPT invite endpoint with the subject the 409 handed back. It never
  // touches the existing account's password.
  const linkMut = useMutation({
    mutationFn: () =>
      apiSend<AdminUser>("POST", "/api/v1/users", token, {
        keycloak_subject: collision,
        display_name: form.display_name || form.username,
        email: form.email || null,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      close();
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <Modal
      opened={opened}
      onClose={close}
      title="Create a user"
      size="md"
      closeOnClickOutside={!createMut.isPending}
      closeOnEscape={!createMut.isPending}
      withCloseButton={!createMut.isPending}
    >
      {issued ? (
        <ShowOncePassword password={issued} onDone={close} />
      ) : collision ? (
        <Alert color="yellow" title="A sign-in account with that username already exists">
          <Stack gap="sm">
            <Text size="sm">
              Link it to a new EasySynQ user instead of creating a duplicate Keycloak account, or
              choose a different username.
            </Text>
            {/* Deliberately conservative: linking calls POST /users, whose request model has no
                role_ids field, so any roles picked above would silently vanish if we didn't say
                so here. We warn instead of chaining a role-assignment request onto this recovery
                flow — linking exists to recover an account that already exists, role management
                already has its own dedicated surface one click away in the Manage drawer, and
                chaining extra requests here would add a new partial-failure state to a recovery
                path. */}
            {form.role_ids.length > 0 && (
              <Text size="sm" fw={600}>
                Selected roles will not be assigned by linking — assign them from Manage after
                linking.
              </Text>
            )}
            {error && (
              <Text size="sm" c="red">
                {error}
              </Text>
            )}
            <Group>
              <Button onClick={() => linkMut.mutate()} loading={linkMut.isPending}>
                Link the existing account
              </Button>
              <Button variant="default" onClick={() => setCollision(null)}>
                Choose a different username
              </Button>
            </Group>
          </Stack>
        </Alert>
      ) : (
        <Stack gap="sm">
          {error && (
            <Alert
              color="red"
              title="Couldn't create user"
              withCloseButton
              onClose={() => setError(null)}
            >
              {error}
            </Alert>
          )}
          <TextInput
            label="Username"
            required
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.currentTarget.value })}
          />
          <TextInput
            label="Display name"
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.currentTarget.value })}
          />
          <TextInput
            label="Email"
            value={form.email}
            error={emailError}
            onChange={(e) => {
              setForm({ ...form, email: e.currentTarget.value });
              setEmailError(null);
            }}
          />
          <Group grow>
            <TextInput
              label="First name"
              value={form.first_name}
              onChange={(e) => setForm({ ...form, first_name: e.currentTarget.value })}
            />
            <TextInput
              label="Last name"
              value={form.last_name}
              onChange={(e) => setForm({ ...form, last_name: e.currentTarget.value })}
            />
          </Group>
          {canGrantRoles && (
            <MultiSelect
              label="Roles"
              description="Optional. The account is created without roles if none are picked."
              placeholder="Assign roles (optional)"
              data={(roles.data ?? []).map((r) => ({ value: r.id, label: r.name }))}
              value={form.role_ids}
              onChange={(v) => setForm({ ...form, role_ids: v })}
              searchable
              clearable
            />
          )}
          <Text size="xs" c="dimmed">
            A temporary password is generated automatically and shown once — hand it to the user
            directly.
          </Text>
          {createMut.isPending && (
            <Alert color="yellow" title="Creating the account">
              This panel stays open until the password is shown, because it cannot be retrieved
              later.
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="subtle" onClick={close} disabled={createMut.isPending}>
              Cancel
            </Button>
            <Button
              onClick={() => createMut.mutate()}
              loading={createMut.isPending}
              disabled={!form.username.trim()}
            >
              Create
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
