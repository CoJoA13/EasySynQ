import {
  Anchor,
  Button,
  Grid,
  Group,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ClauseBadge } from "../../lib/ClauseBadge";
import { RegisterPageHeader } from "../../lib/RegisterPageHeader";
import { getUniqueSearchParam } from "../../lib/effectiveView";
import { SkeletonList } from "../../lib/states";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useMe } from "../../app/shell/useMe";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { DocumentDrawer } from "../document/DocumentDrawer";
import { StateBadge } from "../document/StateBadge";
import { formatDateInTimeZone } from "../../lib/time";
import { ClauseTree } from "./ClauseTree";
import { FacetBar } from "./FacetBar";
import { Pagination } from "./Pagination";
import {
  parseOffset,
  parsePageSize,
  parseUrlFilters,
  toDocumentFilters,
  type UrlFilters,
} from "./filters";
import { useDocuments } from "./useDocuments";

const FILTER_KEYS = ["state", "type", "owner", "clause", "eff"] as const;

export function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const uf = parseUrlFilters(params);
  const offset = parseOffset(params);
  const size = parsePageSize(params);
  const detailId = getUniqueSearchParam(params, "detail");

  const { data: me, isError: meError } = useMe();
  const effectiveFilterReady = !uf.eff || Boolean(me?.org_timezone);
  const {
    data,
    isLoading: documentsLoading,
    isError: documentsError,
  } = useDocuments(
    toDocumentFilters(uf, me?.org_timezone),
    { limit: size, offset },
    effectiveFilterReady,
  );
  const isLoading = documentsLoading || (!effectiveFilterReady && !meError);
  const isError = documentsError || (Boolean(uf.eff) && meError);
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();

  const [density, setDensity] = useState<"comfortable" | "compact">("comfortable");

  const typeMap = new Map((types ?? []).map((t) => [t.id, t.name]));
  const ownerMap = new Map((directory ?? []).map((u) => [u.id, u.display_name ?? "—"]));

  const rows = data?.data ?? [];
  const hasMore = data?.page.has_more ?? false;
  const seed = rows.find((d) => d.id === detailId);
  const hasFilters = FILTER_KEYS.some((k) => uf[k]);

  function patchFilters(patch: Partial<UrlFilters>) {
    setParams(
      (p) => {
        for (const k of FILTER_KEYS) {
          if (k in patch) {
            const v = patch[k];
            if (v) p.set(k, v);
            else p.delete(k);
          }
        }
        p.delete("offset"); // a facet change returns to page 1
        return p;
      },
      { replace: true },
    );
  }
  const clearFilters = () =>
    setParams(
      (p) => {
        for (const k of FILTER_KEYS) p.delete(k);
        p.delete("offset");
        return p;
      },
      { replace: true },
    );
  const setOffset = (o: number) =>
    setParams(
      (p) => {
        if (o > 0) p.set("offset", String(o));
        else p.delete("offset");
        return p;
      },
      { replace: true },
    );
  const setSize = (s: number) =>
    setParams(
      (p) => {
        p.set("size", String(s));
        p.delete("offset");
        return p;
      },
      { replace: true },
    );
  const openDetail = (id: string) =>
    setParams((p) => {
      p.set("detail", id);
      return p;
    });
  const closeDetail = () =>
    setParams(
      (p) => {
        p.delete("detail");
        return p;
      },
      { replace: true },
    );

  return (
    <Stack gap="md">
      {/* S-ui-redesign page anatomy: eyebrow (phase · clause), title, one-line purpose, the single
          primary action top-right. The result count and the density control sit in the toolbar
          row beside the facets, where they describe the table they govern. */}
      <RegisterPageHeader
        eyebrow="Do · Cl 7.5 · Documented information"
        title="Document Library"
        description="Controlled documents governed by clause 7.5 — the current revision, its state and who owns it."
        actions={
          can("document.create") && (
            <Button component={Link} to="/library/new">
              New document
            </Button>
          )
        }
      />

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Paper withBorder p="sm" component="aside" aria-label="Clause spine">
            <Text className="es-eyebrow" c="dimmed" px="xs" pb="xs">
              Clause spine
            </Text>
            <ClauseTree selected={uf.clause} onSelect={(c) => patchFilters({ clause: c })} />
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 9 }}>
          <Stack gap="md">
            <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
              <FacetBar value={uf} onChange={patchFilters} onClear={clearFilters} />
              <Group gap="sm" wrap="nowrap">
                <Text size="sm" c="dimmed" aria-live="polite">
                  {isLoading
                    ? "Loading…"
                    : `Showing ${rows.length}${hasMore ? "+" : ""} document${rows.length === 1 ? "" : "s"}`}
                </Text>
                <SegmentedControl
                  size="xs"
                  aria-label="Row density"
                  value={density}
                  onChange={(v) => setDensity(v as "comfortable" | "compact")}
                  data={[
                    { label: "Comfortable", value: "comfortable" },
                    { label: "Compact", value: "compact" },
                  ]}
                />
              </Group>
            </Group>

            {isError && <Text c="red">Could not load documents.</Text>}

            {isLoading && <SkeletonList rows={5} height={32} label="Loading documents" />}

            {!isLoading && !isError && rows.length === 0 && (
              <Stack gap="xs" align="flex-start">
                <Text>
                  {hasFilters ? "No documents match these filters." : "No documents yet."}
                </Text>
                {hasFilters && (
                  <Button variant="light" size="sm" onClick={clearFilters}>
                    Clear filters
                  </Button>
                )}
              </Stack>
            )}

            {!isLoading && !isError && rows.length > 0 && (
              <Table.ScrollContainer minWidth={720}>
                <Table
                  highlightOnHover
                  stickyHeader
                  verticalSpacing={density === "compact" ? "xs" : "sm"}
                  aria-label="Documents"
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th scope="col">Identifier</Table.Th>
                      <Table.Th scope="col">Title</Table.Th>
                      <Table.Th scope="col">Type</Table.Th>
                      <Table.Th scope="col">Owner</Table.Th>
                      <Table.Th scope="col">Clause</Table.Th>
                      <Table.Th scope="col">State</Table.Th>
                      <Table.Th scope="col">Effective</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.map((d) => (
                      <Table.Tr key={d.id}>
                        <Table.Td>
                          <Anchor
                            component="button"
                            type="button"
                            onClick={() => openDetail(d.id)}
                            aria-label={`Open ${d.identifier}: ${d.title}`}
                          >
                            <Text span ff="monospace" size="sm" fw={500}>
                              {d.identifier}
                            </Text>
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{d.title}</Table.Td>
                        <Table.Td>
                          {d.document_type_id ? (typeMap.get(d.document_type_id) ?? "—") : "—"}
                        </Table.Td>
                        <Table.Td>{ownerMap.get(d.owner_user_id) ?? "—"}</Table.Td>
                        <Table.Td>
                          <Group gap={4}>
                            {(d.clause_refs ?? []).map((c) => (
                              <ClauseBadge key={c} clause={c} />
                            ))}
                          </Group>
                        </Table.Td>
                        <Table.Td>
                          <StateBadge state={d.current_state} />
                        </Table.Td>
                        <Table.Td>
                          <Text size="sm">
                            {d.effective_from
                              ? formatDateInTimeZone(d.effective_from, me?.org_timezone)
                              : "—"}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}

            {!isLoading && !isError && rows.length > 0 && (
              <Pagination
                offset={offset}
                size={size}
                hasMore={hasMore}
                onOffset={setOffset}
                onSize={setSize}
              />
            )}
          </Stack>
        </Grid.Col>
      </Grid>

      <DocumentDrawer documentId={detailId} seed={seed} onClose={closeDetail} />
    </Stack>
  );
}
