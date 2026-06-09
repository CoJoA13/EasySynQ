import { Alert, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { AuditProgram } from "../../lib/types";
import { useAuditPlans, useAuditPrograms, useProcesses } from "./hooks";
import { PlanForm } from "./PlanForm";
import { ProgramForm } from "./ProgramForm";

export function ProgrammePage() {
  const { data, isLoading, isError, forbidden } = useAuditPrograms();
  const { can } = usePermissions();
  // null = closed; "new" = create; a programme = edit. Keyed remount resets the form state.
  const [editing, setEditing] = useState<AuditProgram | "new" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [planFormOpen, setPlanFormOpen] = useState(false);

  // Derive selected BEFORE early returns so hooks below can be called unconditionally.
  const rows = data ?? [];
  const selected = rows.find((p) => p.id === selectedId) ?? rows[0] ?? null;

  // All hooks called unconditionally (Rules of Hooks) — enabled guards handle the null case.
  const plans = useAuditPlans(selected?.id ?? null);
  const processes = useProcesses();
  const { data: directory } = useUserDirectory();

  if (forbidden) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Audit Programme
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the audit programme. It's available to roles holding{" "}
          <code>audit.read</code>.
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
          Audit Programme
        </Title>
        <Alert color="red" title="Couldn't load programmes">
          Please try again.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Audit Programme</Title>
        {can("audit.plan") && (
          <Button onClick={() => setEditing("new")}>＋ New programme</Button>
        )}
      </Group>
      {rows.length === 0 ? (
        <Text c="dimmed">No programmes yet.</Text>
      ) : (
        <Table striped highlightOnHover mb="lg">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Period</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((p) => (
              <Table.Tr
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                style={{ cursor: "pointer" }}
                data-selected={selected?.id === p.id || undefined}
              >
                <Table.Td>{p.identifier}</Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{p.title}</Text>
                </Table.Td>
                <Table.Td>{p.period ?? "—"}</Table.Td>
                <Table.Td>
                  {p.archived ? (
                    <Badge variant="light" color="gray">
                      ▣ Archived
                    </Badge>
                  ) : (
                    <Badge variant="light" color="green">
                      ▶ Active
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  {can("audit.plan") && (
                    <Button
                      size="xs"
                      variant="subtle"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditing(p);
                      }}
                    >
                      Edit
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {selected && (
        <>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Plans — {selected.identifier}</Title>
            {can("audit.plan") && !selected.archived && (
              <Button variant="light" onClick={() => setPlanFormOpen(true)}>
                ＋ Add plan
              </Button>
            )}
          </Group>
          {(plans.data ?? []).length === 0 ? (
            <Text c="dimmed">No plans in this programme yet.</Text>
          ) : (
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Scheduled</Table.Th>
                  <Table.Th>Auditee process</Table.Th>
                  <Table.Th>Lead auditor</Table.Th>
                  <Table.Th>Checklist ref</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(plans.data ?? []).map((p) => (
                  <Table.Tr key={p.id}>
                    <Table.Td>{p.scheduled_date ?? "—"}</Table.Td>
                    <Table.Td>
                      {p.auditee_process_id
                        ? ((processes.data ?? []).find((x) => x.id === p.auditee_process_id)?.name ??
                          `${p.auditee_process_id.slice(0, 8)}…`)
                        : "—"}
                    </Table.Td>
                    <Table.Td>
                      {p.lead_auditor_user_id
                        ? ((directory ?? []).find((u) => u.id === p.lead_auditor_user_id)
                            ?.display_name ?? `${p.lead_auditor_user_id.slice(0, 8)}…`)
                        : "—"}
                    </Table.Td>
                    <Table.Td>{p.checklist_ref ?? "—"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {planFormOpen && (
            <PlanForm programId={selected.id} opened onClose={() => setPlanFormOpen(false)} />
          )}
        </>
      )}
      {editing !== null && (
        <ProgramForm
          key={editing === "new" ? "new" : editing.id}
          program={editing === "new" ? null : editing}
          opened
          onClose={() => setEditing(null)}
        />
      )}
    </Container>
  );
}
