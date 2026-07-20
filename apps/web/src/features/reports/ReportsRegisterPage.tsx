import { Card, Container, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { useDocumentControlRegister } from "./useDocumentControlRegister";
import type { DocumentCurrentState, RegisterProvenance, RegisterRow } from "../../lib/types";
import { AsOf } from "../../lib/AsOf";
import { ErrorState, LoadingState, NoAccessState, EmptyState } from "../../lib/states";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { sortRows, useDebouncedSearch, useTableSort } from "../../lib/registerControls";
import { StateBadge } from "../document/StateBadge";

const SORT_KEYS = ["identifier", "title", "type", "state", "review"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function sortValue(r: RegisterRow, key: SortKey): string | number | null {
  switch (key) {
    case "identifier":
      return r.identifier;
    case "title":
      return r.title;
    case "type":
      return r.document_type ?? "";
    case "state":
      return r.current_state;
    case "review":
      return r.next_review_due;
  }
}

// The Controlled Document Register report (ISO 9001 §7.5.3 master list). Read-only, auditor-facing: a
// provenance banner (defensibility header + content hash) over a filterable/sortable master list.
// Reuses the shared register primitives (RegisterToolbar/SortableTh/registerControls) + the calm
// states. RAG next-review is carried by label + StateBadge shape, never colour alone.
export function ReportsRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } =
    useDocumentControlRegister();
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const matched = query
      ? all.filter((r) =>
          [r.identifier, r.title, r.document_type ?? ""].some((v) =>
            v.toLowerCase().includes(query),
          ),
        )
      : all;
    return sortRows(matched, sort, dir, sortValue);
  }, [data, query, sort, dir]);

  return (
    <Container size="xl" py="md">
      <Stack gap="md">
        <Title order={1}>Controlled Document Register</Title>
        {forbidden ? (
          <NoAccessState message="You need the report.read permission to view the Controlled Document Register." />
        ) : isLoading ? (
          <LoadingState label="Loading the register" />
        ) : isError || !data ? (
          <ErrorState title="Couldn't load the register" onRetry={() => refetch()} />
        ) : (
          <>
            <AsOf at={dataUpdatedAt} />
            <ProvenanceBanner provenance={data.provenance} />
            <RegisterToolbar
              q={q}
              onQ={setQ}
              placeholder="Search identifier / title / type…"
              count={rows.length}
              countNoun="documents"
            />
            {rows.length === 0 ? (
              <EmptyState message="No controlled documents match." />
            ) : (
              <Table.ScrollContainer minWidth={900}>
                <Table striped highlightOnHover>
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
                        label="Type"
                        sortKey="type"
                        sort={sort}
                        dir={dir}
                        onSort={toggleSort}
                        scope="col"
                      />
                      <Table.Th scope="col">Rev</Table.Th>
                      <SortableTh
                        label="State"
                        sortKey="state"
                        sort={sort}
                        dir={dir}
                        onSort={toggleSort}
                        scope="col"
                      />
                      <Table.Th scope="col">Owner</Table.Th>
                      <Table.Th scope="col">Clauses</Table.Th>
                      <SortableTh
                        label="Next review"
                        sortKey="review"
                        sort={sort}
                        dir={dir}
                        onSort={toggleSort}
                        scope="col"
                      />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.map((r) => (
                      <Table.Tr key={r.id}>
                        <Table.Td>{r.identifier}</Table.Td>
                        <Table.Td>{r.title}</Table.Td>
                        <Table.Td>{r.document_type ?? "—"}</Table.Td>
                        <Table.Td>{r.effective_revision_label ?? "—"}</Table.Td>
                        <Table.Td>
                          <StateBadge state={r.current_state as DocumentCurrentState} />
                        </Table.Td>
                        <Table.Td>{r.owner_display ?? "—"}</Table.Td>
                        <Table.Td>
                          {r.clause_refs.length === 0 ? (
                            "—"
                          ) : (
                            <Group gap={4}>
                              {r.clause_refs.map((c) => (
                                <Text key={c.clause} size="sm">
                                  {c.starred ? "★ " : ""}
                                  {c.clause}
                                </Text>
                              ))}
                            </Group>
                          )}
                        </Table.Td>
                        <Table.Td>{r.next_review_due ?? "—"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </>
        )}
      </Stack>
    </Container>
  );
}

function ProvenanceBanner({ provenance }: { provenance: RegisterProvenance }) {
  const p = provenance;
  return (
    <Card withBorder padding="sm">
      <Stack gap={4}>
        <Text fw={600}>{p.report_name}</Text>
        <Text size="sm" c="dimmed">
          Generated by {p.generated_by} · {new Date(p.generated_at).toLocaleString()} · {p.scope} ·
          EasySynQ {p.app_version} · {p.row_count} documents
        </Text>
        <Text size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
          {p.content_hash}
        </Text>
      </Stack>
    </Card>
  );
}
