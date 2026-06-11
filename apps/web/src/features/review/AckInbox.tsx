import { Alert, Button, Checkbox, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import type { Task } from "../../lib/types";
import { useBulkAcknowledge } from "./ackHooks";
import { useTask, useTasks } from "./hooks";

// One inbox row — resolves the doc name best-effort (the list row has no subject_id, so fetch the
// task detail → its document). Selection is controlled by the parent via taskId.
function AckInboxRow({ task, selected, onToggle }: { task: Task; selected: boolean; onToggle: (id: string) => void }) {
  const detail = useTask(task.id); // gives subject_id (detail-only)
  const docId = detail.data?.subject_id ?? null;
  const doc = useDocument(docId, { enabled: docId !== null, retry: false });
  const name = doc.data ? `${doc.data.identifier} — ${doc.data.title}` : docId ? "Document" : "…";
  return (
    <Table.Tr>
      <Table.Td>
        <Checkbox aria-label={`Select ${name}`} checked={selected} onChange={() => onToggle(task.id)} />
      </Table.Td>
      <Table.Td>{docId ? <Link to={`/tasks/${task.id}`}>{name}</Link> : name}</Table.Td>
      <Table.Td>{task.due_at ? task.due_at.slice(0, 10) : "—"}</Table.Td>
    </Table.Tr>
  );
}

// S-ack-2: the dedicated DOC_ACK bulk-ack view (the bell's destination, /tasks?type=DOC_ACK). Multi-select
// loops the per-task decision POST (doc 10 §8.2). Partial failures are reported, never thrown.
export function AckInbox() {
  const { data: tasks, isLoading, isError, error } = useTasks({ state: "PENDING", type: "DOC_ACK" });
  const bulk = useBulkAcknowledge();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<string | null>(null);

  if (isLoading) return <Loader aria-label="Loading acknowledgements" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the acknowledgement queue.</Text>;
    return <Text c="red">Could not load your acknowledgements.</Text>;
  }
  const rows = tasks ?? [];
  const allSelected = rows.length > 0 && rows.every((t) => selected.has(t.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((t) => t.id)));
  }
  async function acknowledgeSelected() {
    setSummary(null);
    const ids = rows.map((t) => t.id).filter((id) => selected.has(id));
    const out = await bulk.run(ids);
    setSelected(new Set());
    const failedNote = out.failed.length ? ` · ${out.failed.length} could not be acknowledged (refresh)` : "";
    setSummary(`${out.ok.length} acknowledged${failedNote}`);
  }

  return (
    <Stack gap="md">
      <Title order={2}>Acknowledgements</Title>
      {summary && <Alert color={summary.includes("could not") ? "yellow" : "green"} withCloseButton onClose={() => setSummary(null)}>{summary}</Alert>}
      {rows.length === 0 ? (
        <Text c="dimmed">No documents awaiting your acknowledgement.</Text>
      ) : (
        <>
          <Group>
            <Button onClick={() => void acknowledgeSelected()} disabled={selected.size === 0}>
              Acknowledge {selected.size} selected
            </Button>
          </Group>
          <Table aria-label="Documents to acknowledge" striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col"><Checkbox aria-label="Select all" checked={allSelected} onChange={toggleAll} /></Table.Th>
                <Table.Th scope="col">Document</Table.Th>
                <Table.Th scope="col">Due</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((t) => (
                <AckInboxRow key={t.id} task={t} selected={selected.has(t.id)} onToggle={toggle} />
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}
