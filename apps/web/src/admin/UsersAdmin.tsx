import {
  ActionIcon,
  Alert,
  Button,
  Drawer,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { usePermissions } from "../app/shell/usePermissions";
import { ApiError, apiGet, apiSend } from "../lib/api";
import type { AdminUser, IssuedTemporaryPassword } from "../lib/types";
import { ErrorState, LoadingState } from "../lib/states";
import { CreateUserModal } from "./CreateUserModal";
import { ShowOncePassword } from "./ShowOncePassword";
import { UserStatusBadge } from "./UserStatusBadge";

interface Role {
  id: string;
  name: string;
}
interface Assignment {
  id: string;
  role_name: string;
}
interface Override {
  id: string;
  permission_key: string;
  effect: string;
  scope: { level: string };
}

// S8d: the Users admin roster — create accounts directly, enable/disable, and per-user role +
// override management (reusing the shipped S2 grant endpoints + their two-tier guard). S-user-create
// (Task 8) retired the paste-a-Keycloak-subject Invite flow from this screen in favor of
// CreateUserModal's one-call provisioning; POST /users itself is untouched (it's the fallback
// CreateUserModal's "link the existing account" path still calls on a username collision).
export function UsersAdmin({ token }: { token: string | null }) {
  const qc = useQueryClient();
  const canCreate = usePermissions().can("user.create");
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [manage, setManage] = useState<AdminUser | null>(null);
  // Lifted from ManageUser's resetMut.isPending (P1, PR #429 review): while a temporary password
  // is being issued, the Drawer must not close via ANY route (onClose/close-button/click-outside/
  // Escape) — the response lands only on ManageUser's own callback, Keycloak has already applied
  // the new password by the time it resolves, and there is nowhere else to retrieve it once the
  // panel showing it is gone. Reset on every terminal outcome (success AND failure) by ManageUser.
  const [issuingPassword, setIssuingPassword] = useState(false);

  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => apiGet<AdminUser[]>("/api/v1/users", token),
    enabled: !!token,
  });

  const onErr = (e: unknown) =>
    setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
  const refresh = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["users"] });
  };

  const statusMut = useMutation({
    mutationFn: (v: { id: string; status: string }) =>
      apiSend("PATCH", `/api/v1/users/${v.id}`, token, { status: v.status }),
    onError: onErr,
    onSuccess: refresh,
  });

  if (users.isLoading) return <LoadingState label="Loading users" />;
  if (users.isError)
    return <ErrorState title="Couldn't load users" onRetry={() => void users.refetch()} />;

  return (
    <Stack gap="md">
      {error && (
        <Alert color="red" title="Action failed" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Group justify="space-between">
        <Text c="dimmed" size="sm">
          Create a user account, assign seeded roles, or disable access.
        </Text>
        {canCreate && <Button onClick={() => setCreateOpen(true)}>Create user</Button>}
      </Group>

      <Table withTableBorder striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Email</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Roles</Table.Th>
            <Table.Th>MFA</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(users.data ?? []).map((u) => (
            <Table.Tr key={u.id}>
              <Table.Td>{u.display_name ?? <Text c="dimmed">(no name)</Text>}</Table.Td>
              <Table.Td>{u.email ?? "—"}</Table.Td>
              <Table.Td>
                <UserStatusBadge status={u.status} />
              </Table.Td>
              <Table.Td>
                <Text size="sm">{u.roles.length ? u.roles.join(", ") : "—"}</Text>
              </Table.Td>
              <Table.Td>{u.mfa_enrolled ? "✓" : "—"}</Table.Td>
              <Table.Td>
                <Group gap="xs" justify="flex-end">
                  <Button size="xs" variant="default" onClick={() => setManage(u)}>
                    Manage
                  </Button>
                  {u.status === "DISABLED" ? (
                    <Button
                      size="xs"
                      variant="light"
                      color="teal"
                      loading={statusMut.isPending}
                      onClick={() => statusMut.mutate({ id: u.id, status: "ACTIVE" })}
                    >
                      Enable
                    </Button>
                  ) : (
                    <Button
                      size="xs"
                      variant="light"
                      color="orange"
                      loading={statusMut.isPending}
                      onClick={() => statusMut.mutate({ id: u.id, status: "DISABLED" })}
                    >
                      Disable
                    </Button>
                  )}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      {canCreate && (
        <CreateUserModal opened={createOpen} onClose={() => setCreateOpen(false)} token={token} />
      )}

      <Drawer
        opened={!!manage}
        onClose={() => {
          // Refuse the close while a temp password is being issued — see the state comment above.
          if (issuingPassword) return;
          setManage(null);
        }}
        closeOnClickOutside={!issuingPassword}
        closeOnEscape={!issuingPassword}
        withCloseButton={!issuingPassword}
        position="right"
        size="lg"
        title={
          manage ? `Manage — ${manage.display_name ?? manage.email ?? manage.keycloak_subject}` : ""
        }
      >
        <Stack gap="md">
          {issuingPassword && (
            <Alert color="yellow" title="Issuing a temporary password">
              This panel stays open until the password is shown, because it cannot be retrieved
              later.
            </Alert>
          )}
          {/* Keyed by user id: switching the managed user must not carry over stale local drawer
              state — most importantly an already-issued temp password from the PREVIOUS user (see
              ManageUser's credential state below). */}
          {manage && (
            <ManageUser
              key={manage.id}
              user={manage}
              token={token}
              onChange={refresh}
              canIssuePassword={canCreate}
              onIssuingChange={setIssuingPassword}
            />
          )}
        </Stack>
      </Drawer>
    </Stack>
  );
}

function ManageUser({
  user,
  token,
  onChange,
  canIssuePassword,
  onIssuingChange,
}: {
  user: AdminUser;
  token: string | null;
  onChange: () => void;
  // Threaded from UsersAdmin, which already resolves `user.create` to gate the Create-user
  // button/modal — reuse that single value instead of a second usePermissions() call for the
  // same key.
  canIssuePassword: boolean;
  // Reports resetMut's pending state up to UsersAdmin (P1, PR #429 review), which uses it to keep
  // the Drawer from closing mid-issuance. Set in resetMut's onMutate/onSettled so it is true for
  // exactly the in-flight window and reset on BOTH success and failure — never left wedged.
  onIssuingChange: (issuing: boolean) => void;
}) {
  const qc = useQueryClient();
  const [roleId, setRoleId] = useState<string | null>(null);
  const [ov, setOv] = useState({ permission_key: "", effect: "ALLOW" });
  const [error, setError] = useState<string | null>(null);
  // Never localStorage/sessionStorage/a URL/a log — React state only, and it dies with this
  // component instance (unmount on drawer close, remount on switching managed user via the
  // `key={manage.id}` above), so it cannot resurface for a different user or a later session.
  const [issued, setIssued] = useState<string | null>(null);
  const onError = (e: unknown) =>
    setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));

  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: () => apiGet<Role[]>("/api/v1/roles", token),
    enabled: !!token,
  });
  const assignments = useQuery({
    queryKey: ["user-roles", user.id],
    queryFn: () => apiGet<Assignment[]>(`/api/v1/users/${user.id}/roles`, token),
    enabled: !!token,
  });
  const overrides = useQuery({
    queryKey: ["user-overrides", user.id],
    queryFn: () => apiGet<Override[]>(`/api/v1/users/${user.id}/overrides`, token),
    enabled: !!token,
  });

  const after = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["user-roles", user.id] });
    void qc.invalidateQueries({ queryKey: ["user-overrides", user.id] });
    // usePermissions caches /me/permissions with a staleTime; a grant edit (possibly the caller's
    // own) must invalidate the whole family so affordance gating catches up immediately.
    void qc.invalidateQueries({ queryKey: ["me-permissions"] });
    onChange();
  };

  const assignMut = useMutation({
    mutationFn: () => apiSend("POST", `/api/v1/users/${user.id}/roles`, token, { role_id: roleId }),
    onMutate: () => setError(null),
    onError,
    onSuccess: () => {
      setRoleId(null);
      after();
    },
  });
  const revokeMut = useMutation({
    mutationFn: (aid: string) =>
      apiSend("DELETE", `/api/v1/users/${user.id}/roles/${aid}`, token, undefined),
    onMutate: () => setError(null),
    onError,
    onSuccess: after,
  });
  const addOvMut = useMutation({
    mutationFn: () =>
      apiSend("POST", `/api/v1/users/${user.id}/overrides`, token, {
        permission_key: ov.permission_key,
        effect: ov.effect,
        scope: { level: "SYSTEM" },
      }),
    onMutate: () => setError(null),
    onError,
    onSuccess: () => {
      setOv({ permission_key: "", effect: "ALLOW" });
      after();
    },
  });
  const removeOvMut = useMutation({
    mutationFn: (oid: string) =>
      apiSend("DELETE", `/api/v1/users/${user.id}/overrides/${oid}`, token, undefined),
    onMutate: () => setError(null),
    onError,
    onSuccess: after,
  });
  const resetMut = useMutation({
    mutationFn: () =>
      apiSend<IssuedTemporaryPassword>(
        "POST",
        `/api/v1/users/${user.id}/temporary-password`,
        token,
      ),
    // Matches CreateUserModal's createMut: TanStack Query's mutation cache otherwise retains the
    // issued password as `state.data` independently of the `issued` React state below, and
    // clicking "Done" (ShowOncePassword's onDone, just below) only clears that local state — it
    // does not unmount this component or its mutation observer. gcTime: 0 lets a detached mutation
    // be dropped from the cache right away instead of after the default 5-minute window;
    // resetMut.reset() is what actually triggers that detachment, called the instant the password
    // is copied into `issued`, so the cache holds the credential for as little time as possible.
    gcTime: 0,
    onMutate: () => {
      setError(null);
      onIssuingChange(true);
    },
    onError,
    onSuccess: (data) => {
      setIssued(data.temporary_password);
      resetMut.reset();
    },
    // Fires after EITHER onSuccess or onError — the single place that lifts the "busy" flag back
    // down, so a failed issuance can never wedge the drawer permanently open.
    onSettled: () => onIssuingChange(false),
  });

  return (
    <Stack gap="lg">
      {error && (
        <Alert color="red" title="Action failed" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {canIssuePassword && (
        <Stack gap="xs">
          <Title order={4}>Credentials</Title>
          {issued ? (
            <ShowOncePassword password={issued} onDone={() => setIssued(null)} />
          ) : (
            <Group>
              <Button
                variant="default"
                loading={resetMut.isPending}
                onClick={() => resetMut.mutate()}
              >
                Issue new temp password
              </Button>
            </Group>
          )}
        </Stack>
      )}
      <Stack gap="xs">
        <Title order={4}>Roles</Title>
        {assignments.data?.length ? (
          assignments.data.map((a) => (
            <Group key={a.id} justify="space-between">
              <Text size="sm">{a.role_name}</Text>
              <ActionIcon
                variant="subtle"
                color="red"
                onClick={() => revokeMut.mutate(a.id)}
                aria-label={`Revoke role ${a.role_name}`}
              >
                ✕
              </ActionIcon>
            </Group>
          ))
        ) : (
          <Text size="sm" c="dimmed">
            No roles assigned.
          </Text>
        )}
        <Group align="flex-end">
          <Select
            label="Assign a role"
            placeholder="Pick a role"
            data={(roles.data ?? []).map((r) => ({ value: r.id, label: r.name }))}
            value={roleId}
            onChange={setRoleId}
            searchable
            style={{ flex: 1 }}
          />
          <Button
            onClick={() => assignMut.mutate()}
            loading={assignMut.isPending}
            disabled={!roleId}
          >
            Assign
          </Button>
        </Group>
      </Stack>

      <Stack gap="xs">
        <Title order={4}>Permission overrides</Title>
        <Text size="xs" c="dimmed">
          System-scoped overrides; finer scoping is available via the API. The two-tier guard
          applies.
        </Text>
        {overrides.data?.length ? (
          overrides.data.map((o) => (
            <Group key={o.id} justify="space-between">
              <Text size="sm" ff="monospace">
                {o.permission_key} · {o.effect} · {o.scope.level}
              </Text>
              <ActionIcon
                variant="subtle"
                color="red"
                onClick={() => removeOvMut.mutate(o.id)}
                aria-label={`Remove ${o.effect.toLowerCase()} ${o.scope.level.toLowerCase()} override for ${o.permission_key}`}
              >
                ✕
              </ActionIcon>
            </Group>
          ))
        ) : (
          <Text size="sm" c="dimmed">
            No overrides.
          </Text>
        )}
        <Group align="flex-end">
          <TextInput
            label="Permission key"
            placeholder="document.read"
            value={ov.permission_key}
            onChange={(e) => setOv({ ...ov, permission_key: e.currentTarget.value })}
            style={{ flex: 1 }}
          />
          <SegmentedControl
            value={ov.effect}
            onChange={(v) => setOv({ ...ov, effect: v })}
            data={["ALLOW", "DENY"]}
          />
          <Button
            onClick={() => addOvMut.mutate()}
            loading={addOvMut.isPending}
            disabled={!ov.permission_key}
          >
            Add
          </Button>
        </Group>
      </Stack>
    </Stack>
  );
}
