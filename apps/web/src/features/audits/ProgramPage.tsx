import { Anchor, Button, Container, Group, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { StatusBadge } from "../../lib/StatusBadge";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import type { AuditProgram } from "../../lib/types";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { useAuditPlans, useAuditPrograms, useProcesses } from "./hooks";
import { PlanForm } from "./PlanForm";
import { ProgramForm } from "./ProgramForm";

export function ProgramPage() {
  const { data, isLoading, isError, forbidden, refetch } = useAuditPrograms();
  const { can } = usePermissions();
  // null = closed; "new" = create; a program = edit. Keyed remount resets the form state.
  const [editing, setEditing] = useState<AuditProgram | "new" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [planFormOpen, setPlanFormOpen] = useState(false);
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();

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
          Audit program
        </Title>
        <NoAccessState
          message={
            <>
              You don't have access to the audit program. It's available to roles holding{" "}
              <code>audit.read</code>.
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="xl" py="md">
        <LoadingState label="Loading programs" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Audit program
        </Title>
        <ErrorState title="Couldn't load programs" onRetry={() => refetch()} />
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Audit program</Title>
        {can("audit.plan") && <Button onClick={() => setEditing("new")}>New program</Button>}
      </Group>
      {rows.length === 0 ? (
        <EmptyState message="No programs yet." />
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
          <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
            {rows.map((p) => (
              <Table.Tr key={p.id} data-selected={selected?.id === p.id || undefined}>
                <Table.Td>
                  <Anchor
                    component="button"
                    type="button"
                    data-rownav
                    onClick={() => setSelectedId(p.id)}
                    aria-pressed={selected?.id === p.id}
                    aria-label={`Select program ${p.identifier}: ${p.title}`}
                  >
                    {p.identifier}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{p.title}</Text>
                </Table.Td>
                {/* || not ??: a cleared period arrives as "" — render the same em-dash as null. */}
                <Table.Td>{p.period || "—"}</Table.Td>
                <Table.Td>
                  <StatusBadge
                    tone={p.archived ? "neutral" : "success"}
                    label={p.archived ? "Archived" : "Active"}
                    kind="Program status"
                  />
                </Table.Td>
                <Table.Td>
                  {can("audit.plan") && (
                    <Button size="xs" variant="subtle" onClick={() => setEditing(p)}>
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
                Add plan
              </Button>
            )}
          </Group>
          {(plans.data ?? []).length === 0 ? (
            <EmptyState message="No plans in this program yet." />
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
                        ? ((processes.data ?? []).find((x) => x.id === p.auditee_process_id)
                            ?.name ?? `${p.auditee_process_id.slice(0, 8)}…`)
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
