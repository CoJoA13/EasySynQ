import {
  Alert,
  Anchor,
  Button,
  Container,
  Group,
  Loader,
  SegmentedControl,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { NewObjectiveModal } from "./NewObjectiveModal";
import type { Objective } from "../../lib/types";
import { AsOf } from "../../lib/AsOf";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveScorecard } from "./hooks";
import { fmtValueUnit, RAG_LABEL, RAG_TONE } from "./labels";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";
import { StateBadge } from "../document/StateBadge";
import { StatusBadge } from "../../lib/StatusBadge";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import {
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "../../lib/registerControls";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";

function currentOverTarget(o: Objective): string {
  return `${fmtValueUnit(o.current_value, "").trim() || "—"} / ${o.target_value} ${o.unit}`.trim();
}

// Critique #5 (power-user triage): debounced search + sortable columns + ↑/↓ row-nav, reusing the
// shared lib/registerControls primitives (the TasksInbox wiring shape). Default sort is by `identifier`
// asc — the human ref is the row anchor and the natural, stable register ordering. The `status` sort
// value is the RAW `o.rag` string (green/amber/red/unmeasured), and `current` sorts on the numeric
// current_value (nulls — unmeasured rows — sort last via sortRows).
const SORT_KEYS = ["identifier", "title", "current", "status", "due"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function sortValue(o: Objective, key: SortKey): string | number | null | undefined {
  switch (key) {
    case "identifier":
      return o.identifier;
    case "title":
      return o.title;
    case "current":
      return o.current_value == null ? null : Number(o.current_value);
    case "status":
      return o.rag;
    case "due":
      return o.due_date;
  }
}

export function ObjectivesRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt } = useObjectiveScorecard();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [rag, setRag] = useUrlParam("rag", "");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const [createOpen, setCreateOpen] = useState(false);

  const rows = useMemo(() => {
    const all = (data?.objectives ?? []).filter((o) => rag === "" || o.rag === rag);
    const matched = query
      ? all.filter((o) => [o.identifier, o.title].some((v) => v.toLowerCase().includes(query)))
      : all;
    return sortRows(matched, sort, dir, sortValue);
  }, [data, rag, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Quality objectives
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to Quality Objectives. It's available to the Quality Manager and
          Process Owner roles.
        </Alert>
      </Container>
    );
  }

  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Quality objectives
        </Title>
        <Alert color="red" title="Couldn't load quality objectives">
          Something went wrong loading the objectives. Please try again.
        </Alert>
      </Container>
    );
  }

  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Quality objectives</Title>
        {can("objective.manage") && (
          <Button onClick={() => setCreateOpen(true)}>New objective</Button>
        )}
      </Group>

      <AsOf at={dataUpdatedAt} />
      <ObjectiveScorecardBand total={data.total} onTarget={data.on_target} byRag={data.by_rag} />

      {data.objectives.length === 0 ? (
        <Alert color="gray" title="No quality objectives yet" mt="md">
          {can("objective.manage")
            ? "Create the first objective to start tracking progress against target."
            : "No objectives have been set up yet."}
        </Alert>
      ) : (
        <>
          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search objectives…"
            count={rows.length}
            countNoun="objectives"
          >
            <SegmentedControl
              value={rag}
              onChange={setRag}
              aria-label="Filter by RAG status"
              data={[
                { value: "", label: "All" },
                { value: "green", label: RAG_LABEL.green },
                { value: "amber", label: RAG_LABEL.amber },
                { value: "red", label: RAG_LABEL.red },
                { value: "unmeasured", label: RAG_LABEL.unmeasured },
              ]}
            />
          </RegisterToolbar>

          {rows.length === 0 ? (
            <Alert color="gray" title="No objectives match your filters." mt="md">
              Try clearing the search or RAG filter.
            </Alert>
          ) : (
            <Table striped highlightOnHover mt="md">
              <Table.Thead>
                <Table.Tr>
                  <SortableTh
                    label="Ref"
                    sortKey="identifier"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Objective"
                    sortKey="title"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Current / target"
                    sortKey="current"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Status"
                    sortKey="status"
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
                {rows.map((o) => (
                  <Table.Tr key={o.id}>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Anchor component={Link} to={`/objectives/${o.id}`} data-rownav>
                          {o.identifier}
                        </Anchor>
                        {/* O-6c: exception-marking — the steady state (Effective) stays unmarked;
                            Draft/InReview/UnderRevision/... get the shared StateBadge. */}
                        {o.current_state !== "Effective" && (
                          <StateBadge state={o.current_state} size="xs" />
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Text lineClamp={1}>{o.title}</Text>
                    </Table.Td>
                    <Table.Td>{currentOverTarget(o)}</Table.Td>
                    <Table.Td>
                      <StatusBadge tone={RAG_TONE[o.rag]} label={RAG_LABEL[o.rag]} kind="Status" />
                    </Table.Td>
                    <Table.Td>{o.due_date}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
      {createOpen && (
        <NewObjectiveModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            navigate(`/objectives/${id}`);
          }}
        />
      )}
    </Container>
  );
}
