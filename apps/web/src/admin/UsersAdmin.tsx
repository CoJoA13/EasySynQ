import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Drawer,
  Group,
  Loader,
  Modal,
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
import { ApiError, apiGet, apiSend } from "../lib/api";

interface User {
  id: string;
  keycloak_subject: string;
  display_name: string | null;
  email: string | null;
  status: string;
  mfa_enrolled: boolean;
  is_guest: boolean;
  roles: string[];
}
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

const STATUS_COLOR: Record<string, string> = {
  ACTIVE: "teal",
  INVITED: "blue",
  DISABLED: "orange",
  LOCKED: "red",
  RETIRED: "gray",
};

// S8d: the Users admin roster — invite, enable/disable, and per-user role + override management
// (reusing the shipped S2 grant endpoints + their two-tier guard). The Avery→Mara hand-off in-app.
export function UsersAdmin({ token }: { token: string | null }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invite, setInvite] = useState({ keycloak_subject: "", display_name: "", email: "" });
  const [manage, setManage] = useState<User | null>(null);

  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => apiGet<User[]>("/api/v1/users", token),
    enabled: !!token,
  });

  const onErr = (e: unknown) => setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
  const refresh = () => {
    setError(null);
    void qc.invalidateQueries({ queryKey: ["users"] });
  };

  const inviteMut = useMutation({
    mutationFn: () => apiSend("POST", "/api/v1/users", token, invite),
    onError: onErr,
    onSuccess: () => {
      setInviteOpen(false);
      setInvite({ keycloak_subject: "", display_name: "", email: "" });
      refresh();
    },
  });

  const statusMut = useMutation({
    mutationFn: (v: { id: string; status: string }) =>
      apiSend("PATCH", `/api/v1/users/${v.id}`, token, { status: v.status }),
    onError: onErr,
    onSuccess: refresh,
  });

  if (users.isLoading) return <Loader />;
  if (users.isError)
    return <Alert color="red" title="Could not load users">{String(users.error)}</Alert>;

  return (
    <Stack gap="md">
      {error && (
        <Alert color="red" title="Action failed" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Group justify="space-between">
        <Text c="dimmed" size="sm">
          Invite a user (bind their Keycloak subject), assign seeded roles, or disable access.
        </Text>
        <Button onClick={() => setInviteOpen(true)}>Invite user</Button>
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
                <Badge variant="light" color={STATUS_COLOR[u.status] ?? "gray"}>
                  {u.status}
                </Badge>
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

      <Modal opened={inviteOpen} onClose={() => setInviteOpen(false)} title="Invite a user">
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            Create the account in Keycloak first, then paste its subject (the OIDC `sub`). The user
            becomes active on their first sign-in.
          </Text>
          <TextInput
            label="Keycloak subject"
            value={invite.keycloak_subject}
            onChange={(e) => setInvite({ ...invite, keycloak_subject: e.currentTarget.value })}
          />
          <TextInput
            label="Display name"
            value={invite.display_name}
            onChange={(e) => setInvite({ ...invite, display_name: e.currentTarget.value })}
          />
          <TextInput
            label="Email"
            value={invite.email}
            onChange={(e) => setInvite({ ...invite, email: e.currentTarget.value })}
          />
          <Group justify="flex-end">
            <Button
              onClick={() => inviteMut.mutate()}
              loading={inviteMut.isPending}
              disabled={!invite.keycloak_subject}
            >
              Invite
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Drawer
        opened={!!manage}
        onClose={() => setManage(null)}
        position="right"
        size="lg"
        title={manage ? `Manage — ${manage.display_name ?? manage.email ?? manage.keycloak_subject}` : ""}
      >
        {manage && <ManageUser user={manage} token={token} onError={onErr} onChange={refresh} />}
      </Drawer>
    </Stack>
  );
}

function ManageUser({
  user,
  token,
  onError,
  onChange,
}: {
  user: User;
  token: string | null;
  onError: (e: unknown) => void;
  onChange: () => void;
}) {
  const qc = useQueryClient();
  const [roleId, setRoleId] = useState<string | null>(null);
  const [ov, setOv] = useState({ permission_key: "", effect: "ALLOW" });

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
    void qc.invalidateQueries({ queryKey: ["user-roles", user.id] });
    void qc.invalidateQueries({ queryKey: ["user-overrides", user.id] });
    onChange();
  };

  const assignMut = useMutation({
    mutationFn: () => apiSend("POST", `/api/v1/users/${user.id}/roles`, token, { role_id: roleId }),
    onError,
    onSuccess: () => {
      setRoleId(null);
      after();
    },
  });
  const revokeMut = useMutation({
    mutationFn: (aid: string) =>
      apiSend("DELETE", `/api/v1/users/${user.id}/roles/${aid}`, token, undefined),
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
    onError,
    onSuccess: () => {
      setOv({ permission_key: "", effect: "ALLOW" });
      after();
    },
  });
  const removeOvMut = useMutation({
    mutationFn: (oid: string) =>
      apiSend("DELETE", `/api/v1/users/${user.id}/overrides/${oid}`, token, undefined),
    onError,
    onSuccess: after,
  });

  return (
    <Stack gap="lg">
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
                aria-label="Revoke role"
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
          <Button onClick={() => assignMut.mutate()} loading={assignMut.isPending} disabled={!roleId}>
            Assign
          </Button>
        </Group>
      </Stack>

      <Stack gap="xs">
        <Title order={4}>Permission overrides</Title>
        <Text size="xs" c="dimmed">
          System-scoped overrides; finer scoping is available via the API. The two-tier guard applies.
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
                aria-label="Remove override"
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
