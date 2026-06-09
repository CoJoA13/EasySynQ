import {
  Alert, Anchor, Button, Container, Group, Loader, Paper, SegmentedControl, SimpleGrid, Table, Text, Title,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { Audit, DirectoryUser } from "../../lib/types";
import { AuditStateBadge } from "./badges";
import { useAudits } from "./hooks";
import { NewAuditModal } from "./NewAuditModal";

function leadLabel(userId: string | null, directory: DirectoryUser[]): string {
  if (!userId) return "—";
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <Paper withBorder p="md" data-tile>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
    </Paper>
  );
}

export function AuditsListPage() {
  const { data, isLoading, isError, forbidden } = useAudits();
  const { data: directory } = useUserDirectory();
  const [filter, setFilter] = useState<"all" | "active" | "closed">("all");
  const { can } = usePermissions();
  const [newOpen, setNewOpen] = useState(false);

  if (forbidden) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to internal audits. They're available to roles holding{" "}
          <code>audit.read</code> (QMS Owner, Process Owner, Internal Auditor).
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="xl" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <Alert color="red" title="Couldn't load audits">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const all = data ?? [];
  // Active = state ≠ Closed (the spec definition). Sort newest-first by created_at (no server order).
  const isActive = (a: Audit) => a.state !== "Closed";
  const sorted = [...all].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const rows =
    filter === "active" ? sorted.filter(isActive)
    : filter === "closed" ? sorted.filter((a) => !isActive(a))
    : sorted;

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Internal Audit</Title>
        {can("audit.create") && <Button onClick={() => setNewOpen(true)}>＋ New audit</Button>}
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }} mb="md">
        {/* "… audits" labels: distinct from the segmented control's All/Active/Closed radio names. */}
        <Tile label="Total audits" value={all.length} />
        <Tile label="Active audits" value={all.filter(isActive).length} />
        <Tile label="Closed audits" value={all.filter((a) => !isActive(a)).length} />
      </SimpleGrid>
      <SegmentedControl
        mb="md"
        value={filter}
        onChange={(v) => setFilter(v as typeof filter)}
        data={[
          { value: "all", label: "All" },
          { value: "active", label: "Active" },
          { value: "closed", label: "Closed" },
        ]}
      />
      {rows.length === 0 ? (
        <Text c="dimmed">No audits yet.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Audit</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Lead auditor</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Started</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/audits/${a.id}`}>
                    {a.identifier ?? a.id.slice(0, 8)}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{a.title ?? "—"}</Text>
                </Table.Td>
                <Table.Td>{leadLabel(a.lead_auditor_user_id, directory ?? [])}</Table.Td>
                <Table.Td>
                  <AuditStateBadge state={a.state} />
                </Table.Td>
                <Table.Td>{a.started_at ?? (a.created_at ? a.created_at.slice(0, 10) : "—")}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <NewAuditModal opened={newOpen} onClose={() => setNewOpen(false)} />
    </Container>
  );
}
