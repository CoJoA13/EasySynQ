import {
  Alert,
  Button,
  Card,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Table,
  Text,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import type { DistributionEntry, DistributionPayload } from "../../lib/types";
import { useDeleteDistributionEntry, useRoles, useUpdateDistribution } from "./ackHooks";

// S-ack-2: the document.distribute-gated issuance editor — the doc-level ack flag, an add-recipient form
// (user | org_role only; process/folder are R43-deferred and never offered), and the entries list with a
// per-entry remove. No PATCH on an entry — change is delete + re-add. No Remind (R43).
export function DistributionEditor({
  documentId,
  payload,
}: {
  documentId: string;
  payload: DistributionPayload;
}) {
  const update = useUpdateDistribution(documentId);
  const del = useDeleteDistributionEntry(documentId);
  const directory = useUserDirectory();
  const roles = useRoles();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<"user" | "org_role">("user");
  const [targetId, setTargetId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const nameFor = useMemo(() => {
    const u = new Map((directory.data ?? []).map((d) => [d.id, d.display_name ?? d.id] as const));
    const r = new Map((roles.data ?? []).map((x) => [x.id, x.name] as const));
    return (e: DistributionEntry) =>
      e.target_type === "user" ? (u.get(e.target_id) ?? e.target_id) : (r.get(e.target_id) ?? e.target_id);
  }, [directory.data, roles.data]);

  const options =
    kind === "user"
      ? (directory.data ?? []).map((d) => ({ value: d.id, label: d.display_name ?? d.id }))
      : (roles.data ?? []).map((r) => ({ value: r.id, label: r.name }));

  async function add() {
    setError(null);
    if (!targetId) return;
    try {
      await update.mutateAsync({ add_entries: [{ target_type: kind, target_id: targetId }] });
      setAdding(false);
      setTargetId(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("That recipient is already on the list.");
      else if (e instanceof ApiError && e.status === 404) setError("That recipient no longer exists.");
      else setError(e instanceof Error ? e.message : "Could not add the recipient.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Distribution</Text>
        <Switch
          label="Require acknowledgement of this document"
          checked={payload.acknowledgement_required}
          onChange={(ev) => update.mutate({ acknowledgement_required: ev.currentTarget.checked })}
        />
        {payload.entries.length === 0 ? (
          <Text size="sm" c="dimmed">
            No recipients yet.
          </Text>
        ) : (
          <Table aria-label="Distribution entries">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Recipient</Table.Th>
                <Table.Th scope="col">Kind</Table.Th>
                <Table.Th scope="col">Ack required</Table.Th>
                <Table.Th scope="col" />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {payload.entries.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{nameFor(e)}</Table.Td>
                  <Table.Td>{e.target_type === "org_role" ? "Role" : "User"}</Table.Td>
                  <Table.Td>{e.ack_required ? "Yes" : "No"}</Table.Td>
                  <Table.Td>
                    <Button
                      variant="subtle"
                      color="red"
                      size="xs"
                      aria-label={`Remove ${nameFor(e)}`}
                      onClick={() => del.mutate(e.id)}
                      loading={del.isPending}
                    >
                      Remove
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {!adding ? (
          <Group>
            <Button variant="light" size="xs" aria-label="Add recipient" onClick={() => setAdding(true)}>
              Add recipient
            </Button>
          </Group>
        ) : (
          <Stack gap="sm">
            <SegmentedControl
              value={kind}
              onChange={(v) => {
                setKind(v as "user" | "org_role");
                setTargetId(null);
              }}
              data={[
                { label: "User", value: "user" },
                { label: "Role", value: "org_role" },
              ]}
              aria-label="Recipient kind"
            />
            <Select
              label="Recipient"
              placeholder={kind === "user" ? "Pick a person" : "Pick a role"}
              data={options}
              value={targetId}
              onChange={setTargetId}
              searchable
            />
            <Group>
              <Button size="xs" onClick={() => void add()} loading={update.isPending} disabled={!targetId}>
                Add
              </Button>
              <Button
                size="xs"
                variant="subtle"
                onClick={() => {
                  setAdding(false);
                  setTargetId(null);
                }}
              >
                Cancel
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
