import {
  Badge,
  Box,
  Button,
  Grid,
  Group,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SkeletonList } from "../../lib/states";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { DocumentDrawer } from "../document/DocumentDrawer";
import { StateBadge } from "../document/StateBadge";
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
  const detailId = params.get("detail");

  const { data, isLoading, isError } = useDocuments(toDocumentFilters(uf), { limit: size, offset });
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
    setParams((p) => {
      for (const k of FILTER_KEYS) {
        if (k in patch) {
          const v = patch[k];
          if (v) p.set(k, v);
          else p.delete(k);
        }
      }
      p.delete("offset"); // a facet change returns to page 1
      return p;
    });
  }
  const clearFilters = () =>
    setParams((p) => {
      for (const k of FILTER_KEYS) p.delete(k);
      p.delete("offset");
      return p;
    });
  const setOffset = (o: number) =>
    setParams((p) => {
      if (o > 0) p.set("offset", String(o));
      else p.delete("offset");
      return p;
    });
  const setSize = (s: number) =>
    setParams((p) => {
      p.set("size", String(s));
      p.delete("offset");
      return p;
    });
  const openDetail = (id: string) =>
    setParams((p) => {
      p.set("detail", id);
      return p;
    });
  const closeDetail = () =>
    setParams((p) => {
      p.delete("detail");
      return p;
    });

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={1}>Document Library</Title>
          <Text size="sm" c="dimmed">
            {isLoading
              ? "Loading…"
              : `Showing ${rows.length}${hasMore ? "+" : ""} document${rows.length === 1 ? "" : "s"}`}
          </Text>
        </div>
        <Group gap="sm">
          {can("document.create") && (
            <Button component={Link} to="/library/new" size="sm">
              ＋ New document
            </Button>
          )}
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

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Box component="aside" aria-label="Clause spine">
            <ClauseTree selected={uf.clause} onSelect={(c) => patchFilters({ clause: c })} />
          </Box>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 9 }}>
          <Stack gap="md">
            <FacetBar value={uf} onChange={patchFilters} onClear={clearFilters} />

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
              <Table
                highlightOnHover
                stickyHeader
                verticalSpacing={density === "compact" ? "xs" : "sm"}
                aria-label="Documents"
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Identifier</Table.Th>
                    <Table.Th>Title</Table.Th>
                    <Table.Th>Type</Table.Th>
                    <Table.Th>Owner</Table.Th>
                    <Table.Th>Clause</Table.Th>
                    <Table.Th>State</Table.Th>
                    <Table.Th>Effective</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((d) => (
                    <Table.Tr
                      key={d.id}
                      style={{ cursor: "pointer" }}
                      onClick={() => openDetail(d.id)}
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          openDetail(d.id);
                        }
                      }}
                    >
                      <Table.Td>
                        <Text ff="monospace" size="sm">
                          {d.identifier}
                        </Text>
                      </Table.Td>
                      <Table.Td>{d.title}</Table.Td>
                      <Table.Td>
                        {d.document_type_id ? (typeMap.get(d.document_type_id) ?? "—") : "—"}
                      </Table.Td>
                      <Table.Td>{ownerMap.get(d.owner_user_id) ?? "—"}</Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          {(d.clause_refs ?? []).map((c) => (
                            <Badge key={c} variant="outline" color="var(--es-accent)">
                              {c}
                            </Badge>
                          ))}
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <StateBadge state={d.current_state} />
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm">
                          {d.effective_from ? d.effective_from.slice(0, 10) : "—"}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
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
