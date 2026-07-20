import { Badge, Button, Card, Container, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocumentControlRegister } from "./useDocumentControlRegister";
import type { RegisterProvenance, RegisterRow } from "../../lib/types";
import { AsOf } from "../../lib/AsOf";
import { ErrorState, LoadingState, NoAccessState, EmptyState } from "../../lib/states";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { sortRows, useDebouncedSearch, useTableSort } from "../../lib/registerControls";
import { StateBadge } from "../document/StateBadge";
import { ReviewStateBadge } from "../document/ReviewStateBadge";
import { FacetBar } from "../library/FacetBar";
import { parseUrlFilters, toDocumentFilters, type UrlFilters } from "../library/filters";
import { useProcesses } from "../objectives/hooks";
import { ProcessSelect } from "./ProcessSelect";

const SORT_KEYS = ["identifier", "title", "type", "state", "review"] as const;
type SortKey = (typeof SORT_KEYS)[number];

// The register's own facet keys — reuses the Library's FacetBar (type/status/owner/clause/effective
// date) plus a register-only process facet (the backend's new `filter[process_id][eq]`).
const FILTER_KEYS = ["state", "type", "owner", "clause", "eff", "process"] as const;

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

// A 64-hex sha256 reads noisily in a dense table row — show a short monospace prefix, the full value
// lives in the native `title` tooltip (no dangerouslySetInnerHTML; both are plain text nodes).
function truncateSha(sha: string): string {
  return sha.length > 12 ? `${sha.slice(0, 12)}…` : sha;
}

// The Controlled Document Register report (ISO 9001 §7.5.3 master list). Read-only, auditor-facing: a
// provenance banner (defensibility header + content hash) over a filterable/sortable master list.
// Reuses the shared register primitives (RegisterToolbar/SortableTh/registerControls) + the calm
// states, and the Library's facet infrastructure (FacetBar/filters.ts + a register-only process
// facet) so the type/status/owner/clause/process facets — and the applied `provenance.filters` echo
// — are wired end to end (S-report-doc-control fix wave, FIX 4). The free-text search box stays a
// CLIENT-side narrowing of the already-fetched (facet-filtered) rows, mirroring the register's other
// text search boxes — the facets are the server-side narrowing. RAG next-review is carried by label +
// StateBadge shape, never colour alone.
export function ReportsRegisterPage() {
  const [params, setParams] = useSearchParams();
  const uf = parseUrlFilters(params);
  // A fresh object each render is fine — React Query hashes queryKey BY VALUE (a stable JSON
  // serialization), not by reference, so this still refetches on a real facet change and NOT on
  // every unrelated re-render.
  const filters = toDocumentFilters(uf);
  // The register-only process facet (R3-1): the shared `toDocumentFilters` no longer maps it (the
  // Library doesn't know about `process`), so the register maps it itself — it owns the
  // ProcessSelect + this file's FILTER_KEYS/hasFilters/clearFilters bookkeeping for it.
  if (uf.process) filters.process_id = uf.process;

  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } =
    useDocumentControlRegister(filters);
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });
  const { data: processes } = useProcesses();
  const processMap = new Map((processes ?? []).map((p) => [p.id, p.name]));

  function patchFilters(patch: Partial<UrlFilters>) {
    setParams((p) => {
      for (const k of FILTER_KEYS) {
        if (k in patch) {
          const v = patch[k];
          if (v) p.set(k, v);
          else p.delete(k);
        }
      }
      return p;
    });
  }
  const clearFilters = () =>
    setParams((p) => {
      for (const k of FILTER_KEYS) p.delete(k);
      return p;
    });
  const hasFilters = FILTER_KEYS.some((k) => uf[k]);

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
            <Group align="flex-end" gap="sm" wrap="wrap">
              <FacetBar value={uf} onChange={patchFilters} onClear={clearFilters} />
              <ProcessSelect
                processes={processes ?? []}
                value={uf.process}
                onChange={(v) => patchFilters({ process: v })}
              />
            </Group>
            <RegisterToolbar
              q={q}
              onQ={setQ}
              placeholder="Search identifier / title / type…"
              count={rows.length}
              countNoun="documents"
            />
            {rows.length === 0 ? (
              <EmptyState
                message={
                  hasFilters
                    ? "No controlled documents match these filters."
                    : "No controlled documents match."
                }
                action={
                  hasFilters ? (
                    <Button variant="light" size="sm" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <Table.ScrollContainer minWidth={1500}>
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
                      <Table.Th scope="col">Effective from</Table.Th>
                      <Table.Th scope="col">Approved by</Table.Th>
                      <Table.Th scope="col">Approved on</Table.Th>
                      <Table.Th scope="col">Processes</Table.Th>
                      <Table.Th scope="col">Blob SHA-256</Table.Th>
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
                    {rows.map((r) => {
                      const processNames = r.process_links.map((id) => processMap.get(id) ?? id);
                      return (
                        <Table.Tr key={r.id}>
                          <Table.Td>{r.identifier}</Table.Td>
                          <Table.Td>{r.title}</Table.Td>
                          <Table.Td>{r.document_type ?? "—"}</Table.Td>
                          <Table.Td>{r.effective_revision_label ?? "—"}</Table.Td>
                          <Table.Td>
                            <StateBadge state={r.current_state} />
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
                          <Table.Td>
                            <Text size="sm">
                              {r.effective_from ? r.effective_from.slice(0, 10) : "—"}
                            </Text>
                          </Table.Td>
                          <Table.Td>{r.approved_by ?? "—"}</Table.Td>
                          <Table.Td>
                            <Text size="sm">
                              {r.approved_on ? r.approved_on.slice(0, 10) : "—"}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            {processNames.length === 0 ? (
                              "—"
                            ) : (
                              <Badge
                                variant="outline"
                                color="var(--es-accent)"
                                title={processNames.join(", ")}
                              >
                                {processNames.length}
                              </Badge>
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Text ff="monospace" size="xs" title={r.blob_sha256 ?? undefined}>
                              {r.blob_sha256 ? truncateSha(r.blob_sha256) : "—"}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <Group gap="xs" wrap="nowrap">
                              <Text size="sm">{r.next_review_due ?? "—"}</Text>
                              <ReviewStateBadge state={r.review_state} />
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
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
