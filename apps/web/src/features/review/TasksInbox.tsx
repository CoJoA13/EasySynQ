import { Stack, Table, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { humanizeStageKey, humanizeToken } from "../../lib/labels";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { sortRows, useDebouncedSearch, useTableSort } from "../../lib/registerControls";
import { EmptyState, LoadingState } from "../../lib/states";
import type { Task } from "../../lib/types";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { TaskStateBadge } from "../document/TaskStateBadge";
import { AckInbox } from "./AckInbox";
import { useTasks } from "./hooks";

// S-web-5 / S-optimize-1: the self-scoped reviewer/approver work queue (GET /tasks). The list now
// carries the subject identity (identifier + short title), so each row NAMES what it acts on — a real
// triage surface, not a bare action+stage count. Power-user affordances (debounced search, sortable
// columns, ↑/↓ row-nav, URL-backed state) reuse the shared lib/registerControls primitives.
//
// S-ack-2: `?type=DOC_ACK` swaps in the bulk AckInbox. The branch lives in a thin dispatcher whose ONLY
// hook is useSearchParams — the general queue's hooks live in GeneralTasksInbox below. `/tasks` and
// `/tasks?type=DOC_ACK` resolve to the SAME route element (App.tsx), so a param-only transition does not
// remount; branching to two distinct child components keeps each child's hook order invariant (a
// conditional return placed BEFORE the hooks would change the dispatcher's hook count between renders and
// throw "Rendered fewer hooks than expected" on the bell→inbox navigation).
export function TasksInbox() {
  const [sp] = useSearchParams();
  if (sp.get("type") === "DOC_ACK") return <AckInbox />;
  return <GeneralTasksInbox />;
}

const SORT_KEYS = ["subject", "action", "stage", "state", "due"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function actionLabel(t: Task): string {
  return t.action_expected ? humanizeToken(t.action_expected) : humanizeToken(t.type);
}

function sortValue(t: Task, key: SortKey): string | null | undefined {
  switch (key) {
    case "subject":
      return t.subject_identifier ?? t.subject_title ?? "";
    case "action":
      return actionLabel(t);
    case "stage":
      return humanizeStageKey(t.stage_key);
    case "state":
      return t.state;
    case "due":
      return t.due_at;
  }
}

function GeneralTasksInbox() {
  const { data: tasks, isLoading, isError, error } = useTasks({ state: "PENDING" });
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "due",
    defaultDir: "asc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();

  const visible = useMemo(() => {
    const all = tasks ?? [];
    const matched = query
      ? all.filter((t) =>
          [
            t.subject_identifier,
            t.subject_title,
            t.action_expected,
            t.type,
            humanizeStageKey(t.stage_key),
          ].some((v) => v?.toLowerCase().includes(query)),
        )
      : all;
    return sortRows(matched, sort, dir, sortValue);
  }, [tasks, query, sort, dir]);

  if (isLoading) return <LoadingState label="Loading tasks" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the task queue.</Text>;
    return <Text c="red">Could not load your tasks.</Text>;
  }

  const total = tasks?.length ?? 0;

  return (
    <Stack gap="md">
      <Title order={2}>Review &amp; Approve</Title>
      {total === 0 ? (
        <EmptyState message="No tasks in your queue." />
      ) : (
        <>
          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search tasks…"
            count={visible.length}
            countNoun="tasks"
          />
          {visible.length === 0 ? (
            <EmptyState message="No tasks match your search." />
          ) : (
            <Table aria-label="My tasks" striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <SortableTh
                    label="Subject"
                    sortKey="subject"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Action"
                    sortKey="action"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Stage"
                    sortKey="stage"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="State"
                    sortKey="state"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Due"
                    sortKey="due"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {visible.map((t) => (
                  <Table.Tr key={t.id}>
                    <Table.Td>
                      <Link to={`/tasks/${t.id}`} data-rownav>
                        {t.subject_identifier ?? actionLabel(t)}
                      </Link>
                      {t.subject_title && (
                        <Text size="sm" c="dimmed" lineClamp={1}>
                          {t.subject_title}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{actionLabel(t)}</Table.Td>
                    <Table.Td>{humanizeStageKey(t.stage_key)}</Table.Td>
                    <Table.Td>
                      <TaskStateBadge state={t.state} />
                    </Table.Td>
                    <Table.Td>{t.due_at ? t.due_at.slice(0, 10) : "—"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
    </Stack>
  );
}
