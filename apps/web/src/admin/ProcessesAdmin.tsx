import {
  ActionIcon,
  Alert,
  Button,
  Drawer,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { usePermissions } from "../app/shell/usePermissions";
import { useUserDirectory } from "../app/shell/useUserDirectory";
import { ApiError, apiGet, apiSend } from "../lib/api";
import type { ProcessRow } from "../lib/types";

interface ProcessOwner {
  id: string;
  process_id: string | null;
  user_id: string;
  org_role_id: string;
  org_role_name: string;
  created_at: string | null;
}

// S-owner-assignment-1: the Processes admin tab — assign/revoke a process OWNER (the accountable
// person, Clause 5.3). Assigning records the RACI fact AND mints the concrete PROCESS-scoped
// "Process Owner" grant (substituting the :assignment_process placeholder). The write affordances
// render only when the caller holds process.assign_owner; a COARSE SYSTEM probe decides that, and
// the server's POST 403 is the true boundary (the realistic v1 grantor holds a SYSTEM override).
export function ProcessesAdmin({ token }: { token: string | null }) {
  const [error, setError] = useState<string | null>(null);
  const [manage, setManage] = useState<ProcessRow | null>(null);
  const perms = usePermissions();
  const canAssign = perms.can("process.assign_owner");

  const processes = useQuery({
    queryKey: ["processes"],
    queryFn: () => apiGet<ProcessRow[]>("/api/v1/processes", token),
    enabled: !!token,
    retry: false,
  });

  if (processes.isLoading) return <Loader />;
  if (processes.isError) {
    const forbidden = processes.error instanceof ApiError && processes.error.status === 403;
    return (
      <Alert
        color={forbidden ? "gray" : "red"}
        title={forbidden ? "No access" : "Could not load processes"}
      >
        {forbidden ? "You need process.read to manage process owners." : String(processes.error)}
      </Alert>
    );
  }

  const rows = processes.data ?? [];

  return (
    <Stack gap="md">
      {error && (
        <Alert color="red" title="Action failed" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Text c="dimmed" size="sm">
        Assign the accountable owner of each process (Clause 5.3). Assigning grants the owner the
        Process Owner permission set, scoped to that process.
      </Text>
      {rows.length === 0 ? (
        <Text c="dimmed" size="sm">
          No processes yet.
        </Text>
      ) : (
        <Table withTableBorder striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Process</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((p) => (
              <Table.Tr key={p.id}>
                <Table.Td>{p.name}</Table.Td>
                <Table.Td>
                  <Group justify="flex-end">
                    <Button size="xs" variant="default" onClick={() => setManage(p)}>
                      Manage owners
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Drawer
        opened={!!manage}
        onClose={() => setManage(null)}
        position="right"
        size="lg"
        title={manage ? `Owners — ${manage.name}` : ""}
      >
        {manage && (
          <ManageOwners
            process={manage}
            token={token}
            canAssign={canAssign}
            onError={(e) => setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e))}
          />
        )}
      </Drawer>
    </Stack>
  );
}

function ManageOwners({
  process,
  token,
  canAssign,
  onError,
}: {
  process: ProcessRow;
  token: string | null;
  canAssign: boolean;
  onError: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const [userId, setUserId] = useState<string | null>(null);
  const directory = useUserDirectory();

  const owners = useQuery({
    queryKey: ["process-owners", process.id],
    queryFn: () => apiGet<ProcessOwner[]>(`/api/v1/processes/${process.id}/owners`, token),
    enabled: !!token,
    retry: false,
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["process-owners", process.id] });

  const assignMut = useMutation({
    mutationFn: () =>
      apiSend("POST", `/api/v1/processes/${process.id}/owner`, token, { user_id: userId }),
    onError,
    onSuccess: () => {
      setUserId(null);
      refresh();
    },
  });
  const revokeMut = useMutation({
    mutationFn: (uid: string) =>
      apiSend("DELETE", `/api/v1/processes/${process.id}/owner/${uid}`, token, undefined),
    onError,
    onSuccess: refresh,
  });

  const nameOf = (uid: string) =>
    (directory.data ?? []).find((u) => u.id === uid)?.display_name ?? uid;

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <Title order={4}>Current owners</Title>
        {owners.isLoading ? (
          <Loader size="sm" />
        ) : owners.data?.length ? (
          owners.data.map((o) => (
            <Group key={o.id} justify="space-between">
              <Text size="sm">{nameOf(o.user_id)}</Text>
              {canAssign && (
                <ActionIcon
                  variant="subtle"
                  color="red"
                  onClick={() => revokeMut.mutate(o.user_id)}
                  aria-label="Remove owner"
                >
                  ✕
                </ActionIcon>
              )}
            </Group>
          ))
        ) : (
          <Text size="sm" c="dimmed">
            No owners assigned.
          </Text>
        )}
      </Stack>

      {canAssign ? (
        <Group align="flex-end">
          <Select
            label="Owner"
            placeholder="Pick a user"
            data={(directory.data ?? []).map((u) => ({
              value: u.id,
              label: u.display_name ?? u.id,
            }))}
            value={userId}
            onChange={setUserId}
            searchable
            style={{ flex: 1 }}
          />
          <Button
            onClick={() => assignMut.mutate()}
            loading={assignMut.isPending}
            disabled={!userId}
          >
            Assign owner
          </Button>
        </Group>
      ) : (
        <Text size="sm" c="dimmed">
          You need process.assign_owner to change owners.
        </Text>
      )}
    </Stack>
  );
}
