import { Anchor, Box, Button, Container, Group, Select, Table, Title } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { AsOf } from "../../lib/AsOf";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import {
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "../../lib/registerControls";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import type { Initiative, InitiativeSource, InitiativeStage } from "../../lib/types";
import { InitiativeDrawer } from "./InitiativeDrawer";
import { InitiativeStageBadge } from "./InitiativeStageBadge";
import { INITIATIVE_STAGE_META, SOURCE_LABEL } from "./labels";
import { RaiseInitiativeModal } from "./RaiseInitiativeModal";
import { useInitiatives } from "./hooks";

const STAGES: InitiativeStage[] = ["Open", "InProgress", "Completed", "Closed", "Cancelled"];
const SOURCES: InitiativeSource[] = ["OFI", "review", "manual"];

const SORT_KEYS = ["identifier", "title", "source", "owner", "stage", "opened"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function ImprovementRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useInitiatives();
  const { data: directory } = useUserDirectory();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(() => params.get("initiative"));
  // URL-backed enum filters (distinct keys; neither collides with the `initiative` drawer deep-link).
  const [stage, setStage] = useUrlParam("stage");
  const [source, setSource] = useUrlParam("source");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "opened",
    defaultDir: "desc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const { can } = usePermissions();
  const [raising, setRaising] = useState(false);

  // Open the drawer for ?initiative=<id> on mount + whenever the param changes (a deep-link while
  // mounted). Guarded on a non-null id so clearing the param on close never re-opens the drawer.
  useEffect(() => {
    const id = params.get("initiative");
    if (id) setSelected(id);
  }, [params]);

  function closeDrawer() {
    setSelected(null);
    if (params.has("initiative")) {
      setParams(
        (p) => {
          p.delete("initiative");
          return p;
        },
        { replace: true },
      );
    }
  }

  const ownerLabel = (id: string | null): string =>
    id ? (directory?.find((u) => u.id === id)?.display_name ?? `${id.slice(0, 8)}…`) : "—";

  const rows = data ?? [];
  const visible = useMemo(() => {
    const matched = rows.filter(
      (i) =>
        (stage === "" || i.stage === stage) &&
        (source === "" || i.source === source) &&
        (query === "" ||
          [i.identifier, i.title, i.target_outcome].some((v) => v?.toLowerCase().includes(query))),
    );
    function sortValue(i: Initiative, key: SortKey): string | null | undefined {
      switch (key) {
        case "identifier":
          return i.identifier;
        case "title":
          return i.title;
        case "source":
          return SOURCE_LABEL[i.source];
        case "owner":
          return ownerLabel(i.owner_user_id);
        case "stage":
          return INITIATIVE_STAGE_META[i.stage].label;
        case "opened":
          return i.opened_at;
      }
    }
    return sortRows(matched, sort, dir, sortValue);
    // ownerLabel depends on `directory`; recompute when it loads so the owner sort/column resolve.
  }, [rows, stage, source, query, sort, dir, directory]);

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Improvement
        </Title>
        <NoAccessState message="You don't have access to the improvement register. It's available to roles holding the improvement read permission." />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <LoadingState label="Loading improvement initiatives" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Improvement
        </Title>
        <ErrorState title="Couldn't load improvement initiatives" onRetry={() => void refetch()} />
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Improvement</Title>
        {can("improvement.manage") && (
          <Button onClick={() => setRaising(true)}>New initiative</Button>
        )}
      </Group>

      <AsOf at={dataUpdatedAt} />

      {rows.length === 0 ? (
        <EmptyState message="No improvement initiatives yet." />
      ) : (
        <>
          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search initiatives…"
            count={visible.length}
            countNoun="initiatives"
          >
            <Select
              aria-label="Stage"
              placeholder="All stages"
              clearable
              value={stage || null}
              onChange={(v) => setStage((v as InitiativeStage) ?? "")}
              data={STAGES.map((s) => ({ value: s, label: INITIATIVE_STAGE_META[s].label }))}
            />
            <Select
              aria-label="Source"
              placeholder="All sources"
              clearable
              value={source || null}
              onChange={(v) => setSource((v as InitiativeSource) ?? "")}
              data={SOURCES.map((s) => ({ value: s, label: SOURCE_LABEL[s] }))}
            />
          </RegisterToolbar>

          {visible.length === 0 ? (
            <Box mt="md">
              <EmptyState message="No initiatives match your filters." />
            </Box>
          ) : (
            <Table highlightOnHover mt="md">
              <Table.Thead>
                <Table.Tr>
                  <SortableTh
                    label="Identifier"
                    sortKey="identifier"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Title"
                    sortKey="title"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Source"
                    sortKey="source"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Owner"
                    sortKey="owner"
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
                    label="Opened"
                    sortKey="opened"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {visible.map((i) => (
                  <Table.Tr key={i.id}>
                    <Table.Td>
                      <Anchor
                        component="button"
                        type="button"
                        data-rownav
                        onClick={() => setSelected(i.id)}
                      >
                        {i.identifier}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>{i.title}</Table.Td>
                    <Table.Td>{SOURCE_LABEL[i.source]}</Table.Td>
                    <Table.Td>{ownerLabel(i.owner_user_id)}</Table.Td>
                    <Table.Td>
                      <InitiativeStageBadge stage={i.stage} />
                    </Table.Td>
                    <Table.Td>{formatDate(i.opened_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      <InitiativeDrawer initiativeId={selected} onClose={closeDrawer} />
      {raising && (
        <RaiseInitiativeModal
          onClose={() => setRaising(false)}
          onCreated={(id) => setSelected(id)}
        />
      )}
    </Container>
  );
}
