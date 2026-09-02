import { Alert, Button, Checkbox, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../../app/shell/useMe";
import { ApiError } from "../../lib/api";
import { EmptyState, LoadingState } from "../../lib/states";
import { formatDateInTimeZone } from "../../lib/time";
import type { Task } from "../../lib/types";
import { useBulkSelection } from "../../lib/useBulkSelection";
import { useBulkAcknowledge } from "./ackHooks";
import { useTasks } from "./hooks";

// S-optimize-1: the document name now comes straight off the enriched list row (subject_identifier +
// subject_title) — no more per-row task-detail → useDocument N+1.
function ackName(t: Task): string {
  if (t.subject_identifier && t.subject_title)
    return `${t.subject_identifier} — ${t.subject_title}`;
  return t.subject_identifier ?? t.subject_title ?? "Document";
}

// S-ack-2: the dedicated DOC_ACK bulk-ack view (the bell's destination, /tasks?type=DOC_ACK). Multi-
// select loops the per-task decision POST (doc 10 §8.2); partial failures are reported, never thrown.
// Selection uses the shared useBulkSelection primitive. Bulk stays acknowledge-only (no signature) —
// there is deliberately NO bulk-approve (each approval is a signed, SoD-gated decision).
export function AckInbox() {
  const {
    data: tasks,
    isLoading,
    isError,
    error,
  } = useTasks({ state: "PENDING", type: "DOC_ACK" });
  const { data: me } = useMe();
  const bulk = useBulkAcknowledge();
  const rows = tasks ?? [];
  const { selected, toggle, toggleAll, clear, allSelected, count, selectedIds } =
    useBulkSelection(rows);
  const [summary, setSummary] = useState<string | null>(null);
  const [isAcknowledging, setIsAcknowledging] = useState(false);
  const acknowledgingRef = useRef(false);

  if (isLoading) return <LoadingState label="Loading acknowledgements" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the acknowledgement queue.</Text>;
    return <Text c="red">Could not load your acknowledgements.</Text>;
  }

  async function acknowledgeSelected() {
    // The ref closes the same-tick double-click gap before React can commit the disabled/loading
    // button state. One user action must produce one set of per-task decision requests.
    if (acknowledgingRef.current) return;
    acknowledgingRef.current = true;
    setIsAcknowledging(true);
    setSummary(null);
    try {
      const out = await bulk.run([...selectedIds]);
      clear();
      const failedNote = out.failed.length
        ? ` · ${out.failed.length} could not be acknowledged (refresh)`
        : "";
      setSummary(`${out.ok.length} acknowledged${failedNote}`);
    } finally {
      acknowledgingRef.current = false;
      setIsAcknowledging(false);
    }
  }

  return (
    <Stack gap="md">
      <Title order={1} size="h2">
        Acknowledgements
      </Title>
      {summary && (
        <Alert
          color={summary.includes("could not") ? "yellow" : "green"}
          withCloseButton
          onClose={() => setSummary(null)}
        >
          {summary}
        </Alert>
      )}
      {rows.length === 0 ? (
        <EmptyState message="No documents awaiting your acknowledgement." />
      ) : (
        <>
          <Group>
            <Button
              onClick={() => void acknowledgeSelected()}
              disabled={count === 0 || isAcknowledging}
              loading={isAcknowledging}
            >
              Acknowledge {count} selected
            </Button>
          </Group>
          <Table aria-label="Documents to acknowledge" striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">
                  <Checkbox
                    aria-label="Select all"
                    checked={allSelected}
                    disabled={isAcknowledging}
                    onChange={toggleAll}
                  />
                </Table.Th>
                <Table.Th scope="col">Document</Table.Th>
                <Table.Th scope="col">Due</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((t) => {
                const name = ackName(t);
                return (
                  <Table.Tr key={t.id}>
                    <Table.Td>
                      <Checkbox
                        aria-label={`Select ${name}`}
                        checked={selected.has(t.id)}
                        disabled={isAcknowledging}
                        onChange={() => toggle(t.id)}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Link to={`/tasks/${t.id}`}>{name}</Link>
                    </Table.Td>
                    <Table.Td>
                      {t.due_at ? formatDateInTimeZone(t.due_at, me?.org_timezone) : "—"}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}
