import { Anchor, Group, Paper, Skeleton, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { Task, TaskType } from "../../lib/types";
import { useMyTasks } from "./hooks";

// Friendly labels for every task type that can reach a personal inbox (doc 10 §8). The Record is
// exhaustive over TaskType, so every row resolves to a label (tsc enforces it).
const TASK_LABEL: Record<TaskType, string> = {
  APPROVE: "Approval",
  REVIEW: "Document review",
  PERIODIC_REVIEW: "Periodic review",
  DOC_ACK: "Acknowledgement",
  AUDIT_TASK: "Audit task",
  FINDING_ACK: "Finding acknowledgement",
  CAPA_STAGE: "CAPA stage",
  CAPA_ACTION: "CAPA action",
  VERIFY: "Verification",
  MR_INPUT: "Management-review input",
  MR_ACTION: "Management-review action",
  DCR_TRIAGE: "Change-request triage",
};

// Soonest-due first; a null due_at sorts last (ISO strings compare lexically).
function sortByDue(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.due_at === b.due_at) return 0;
    if (a.due_at === null) return 1;
    if (b.due_at === null) return -1;
    return a.due_at < b.due_at ? -1 : 1;
  });
}

// S-optimize-1: the /tasks LIST now carries the subject identity, so each rail row NAMES what it acts
// on (identifier + short title) instead of just the task type — triageable at a glance from Home.
// Self-scoped (no permission key); always visible.
export function MyTasksRail() {
  const { data, isLoading, isError } = useMyTasks();
  const tasks = data ?? [];
  const top = sortByDue(tasks).slice(0, 3);

  return (
    <Paper withBorder radius="md" p="md">
      <Group justify="space-between" align="center" mb="sm">
        <Text fw={500}>My tasks{tasks.length ? ` (${tasks.length})` : ""}</Text>
        <Anchor component={Link} to="/tasks" size="sm">
          See all my tasks <span aria-hidden="true">→</span>
        </Anchor>
      </Group>
      {isLoading ? (
        <Skeleton height={16} width="70%" />
      ) : isError ? (
        <Text size="sm" c="dimmed">
          Couldn&apos;t load your tasks.
        </Text>
      ) : tasks.length === 0 ? (
        <Text size="sm" c="dimmed">
          You&apos;re all caught up.
        </Text>
      ) : (
        <Stack gap={6}>
          {top.map((t) => (
            <Text key={t.id} size="sm">
              <Text span fw={500}>
                {t.subject_identifier ?? TASK_LABEL[t.type]}
              </Text>
              {t.subject_title ? ` — ${t.subject_title}` : ""}
              {` · ${TASK_LABEL[t.type]}`}
              {t.due_at ? ` · due ${t.due_at.slice(0, 10)}` : ""}
            </Text>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
