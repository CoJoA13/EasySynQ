import { Loader, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { TaskStateBadge } from "../document/TaskStateBadge";
import { useTasks } from "./hooks";

// S-web-5: the self-scoped reviewer/approver work queue (GET /tasks). The document identity is shown
// on the review page (one click away) — a per-row Document column is a deferred enhancement (it needs
// an instance→doc resolution that would N+1 the list).
export function TasksInbox() {
  const { data: tasks, isLoading, isError, error } = useTasks({ state: "PENDING" });

  if (isLoading) return <Loader aria-label="Loading tasks" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the task queue.</Text>;
    return <Text c="red">Could not load your tasks.</Text>;
  }

  return (
    <Stack gap="md">
      <Title order={2}>Review &amp; Approve</Title>
      {!tasks || tasks.length === 0 ? (
        <Text c="dimmed">No tasks in your queue.</Text>
      ) : (
        <Table aria-label="My tasks" striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Task</Table.Th>
              <Table.Th scope="col">Stage</Table.Th>
              <Table.Th scope="col">State</Table.Th>
              <Table.Th scope="col">Due</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tasks.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td>
                  <Link to={`/tasks/${t.id}`}>{t.action_expected ?? t.type}</Link>
                </Table.Td>
                <Table.Td>{t.stage_key}</Table.Td>
                <Table.Td>
                  <TaskStateBadge state={t.state} />
                </Table.Td>
                <Table.Td>{t.due_at ? t.due_at.slice(0, 10) : "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
