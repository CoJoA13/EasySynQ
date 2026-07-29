import { Badge, Button, Card, Container, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocumentControlRegister } from "./useDocumentControlRegister";
import { useMe } from "../../app/shell/useMe";
import { ClauseBadge } from "../../lib/ClauseBadge";
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
import { buildProcessFacetOptions, deriveRegisterFacetSource } from "./reportFacets";

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
  const { data: processes } = useProcesses();
  const { data: me, isError: meError, refetch: refetchMe } = useMe();

  // #334: keep one unfiltered register query as the stable facet universe. It is already
  // permission-filtered by report.read + document.read, so deriving choices from it cannot disclose
  // anything beyond the table. A second observer with the same {} query key is deduplicated by
  // React Query when no filters are active; with filters, this baseline remains cached and stable.
  const facetQuery = useDocumentControlRegister();
  const facetSource = useMemo(
    () => (facetQuery.data ? deriveRegisterFacetSource(facetQuery.data) : null),
    [facetQuery.data],
  );
  const clauseValues = facetSource?.clauseValues ?? [];
  const processOptions = useMemo(
    () => (facetSource ? buildProcessFacetOptions(facetSource, processes ?? []) : []),
    [facetSource, processes],
  );
  const clauseValueSet = useMemo(() => new Set(clauseValues), [clauseValues]);
  const processValueSet = useMemo(
    () => new Set(processOptions.map((option) => option.value)),
    [processOptions],
  );
  const processMap = useMemo(
    () => new Map(processOptions.map((option) => [option.value, option.label])),
    [processOptions],
  );

  // A fresh object each render is fine — React Query hashes queryKey BY VALUE (a stable JSON
  // serialization), not by reference, so this still refetches on a real facet change and NOT on
  // every unrelated re-render.
  const effectiveFilterReady = !uf.eff || Boolean(me?.org_timezone);
  const filters = toDocumentFilters(uf, me?.org_timezone);
  // The shared Library mapper knows clause but not the register-only process facet. Both report
  // facets are applied only after the unfiltered, caller-visible baseline proves the URL value is
  // representable. This generalizes the old process-only guard to clause and makes neither catalog
  // endpoint authoritative for filter applicability.
  if (uf.clause && !clauseValueSet.has(uf.clause)) delete filters.clause;
  if (uf.process && processValueSet.has(uf.process)) filters.process_id = uf.process;

  // Remove stale/bookmarked values once the permission-filtered universe is known. They were never
  // sent to the API (the guards above), and replacing the URL prevents an invisible ghost filter.
  const invalidClause = Boolean(facetSource && uf.clause && !clauseValueSet.has(uf.clause));
  const invalidProcess = Boolean(facetSource && uf.process && !processValueSet.has(uf.process));
  useEffect(() => {
    if (!invalidClause && !invalidProcess) return;
    setParams(
      (current) => {
        if (invalidClause) current.delete("clause");
        if (invalidProcess) current.delete("process");
        return current;
      },
      { replace: true },
    );
  }, [invalidClause, invalidProcess, setParams]);

  const registerQuery = useDocumentControlRegister(
    filters,
    facetSource !== null && effectiveFilterReady,
  );
  const data = registerQuery.data;
  const forbidden = facetQuery.forbidden || registerQuery.forbidden;
  const isError = facetQuery.isError || registerQuery.isError || (Boolean(uf.eff) && meError);
  const isLoading =
    !isError &&
    (facetQuery.isLoading ||
      facetSource === null ||
      registerQuery.isLoading ||
      !effectiveFilterReady);
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });

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
          <ErrorState
            title="Couldn't load the register"
            onRetry={() => {
              void refetchMe();
              if (facetQuery.isError) void facetQuery.refetch();
              else void registerQuery.refetch();
            }}
          />
        ) : (
          <>
            <AsOf at={registerQuery.dataUpdatedAt} />
            <ProvenanceBanner provenance={data.provenance} />
            <Group align="flex-end" gap="sm" wrap="wrap">
              <FacetBar
                value={uf}
                onChange={patchFilters}
                onClear={clearFilters}
                clauseValues={clauseValues}
              />
              {processOptions.length > 0 && (
                <ProcessSelect
                  options={processOptions}
                  value={uf.process}
                  onChange={(v) => patchFilters({ process: v })}
                />
              )}
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
                                  <ClauseBadge
                                    key={c.clause}
                                    clause={c.clause}
                                    starred={c.starred}
                                  />
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

// FIX 2 (Codex round 5, P2): `generated_at`/`as_of` are already an ORG-TZ ISO-8601 string with an
// explicit offset (the server built it via `snapshot_at.astimezone(current_org_tz())`) — the
// header explicitly promises a generated-at *with timezone*. Rendering via `new Date(...)
// .toLocaleString()` re-converts that instant into the VIEWER'S BROWSER timezone and drops the
// offset entirely, so e.g. a Tokyo-midnight `generated_at` shows as the previous day to a UTC
// auditor. Only an offset (not an IANA zone name) is available on the string, so format directly
// from its own components — never route through browser-tz `Date` conversion. Fail-safe: an
// unparseable string (shouldn't happen — the backend always emits this exact shape) renders as-is
// rather than throwing.
function formatOrgTimestamp(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(iso);
  if (!m) return iso;
  const [, date, time, offsetRaw] = m;
  const offset = offsetRaw === "Z" ? "UTC+00:00" : `UTC${offsetRaw}`;
  return `${date} ${time} (${offset})`;
}

function ProvenanceBanner({ provenance }: { provenance: RegisterProvenance }) {
  const p = provenance;
  // Codex round 6 FIX 2: `scope` alone (always `org:<short_code>`) can't distinguish an org-wide
  // register from one a PROCESS-scoped report.read grant confines — surface `process_scope`
  // explicitly, as plain text (never dangerouslySetInnerHTML), so an auditor can't mistake a
  // process-limited register for the org-wide one.
  const processScope = p.process_scope;
  // #335 fix 1: a PROCESS report.read DENY removes that process's documents per-row, so the register
  // is "{process_scope, or org-wide} EXCEPT excluded_processes". Surface the exclusions as plain
  // text (never dangerouslySetInnerHTML) so a restricted register can't read as the org-wide one.
  const excludedProcesses = p.excluded_processes;
  return (
    <Card withBorder padding="sm">
      <Stack gap={4}>
        <Text fw={600}>{p.report_name}</Text>
        <Text size="sm" c="dimmed">
          Generated by {p.generated_by} · {formatOrgTimestamp(p.generated_at)} · {p.scope} ·
          EasySynQ {p.app_version} · {p.row_count} documents
        </Text>
        {processScope && processScope.length > 0 && (
          <Text size="sm" c="dimmed">
            Scope limited to processes: {processScope.map((proc) => proc.name).join(", ")}
          </Text>
        )}
        {excludedProcesses && excludedProcesses.length > 0 && (
          <Text size="sm" c="dimmed">
            Excludes processes: {excludedProcesses.map((proc) => proc.name).join(", ")}
          </Text>
        )}
        <Text size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
          {p.content_hash}
        </Text>
      </Stack>
    </Card>
  );
}
