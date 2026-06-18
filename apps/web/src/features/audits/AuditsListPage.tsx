import {
  Anchor,
  Button,
  Container,
  Group,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import {
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "../../lib/registerControls";
import type { Audit, DirectoryUser } from "../../lib/types";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { AuditStateBadge } from "./badges";
import { useAudits } from "./hooks";
import { NewAuditModal } from "./NewAuditModal";

function leadLabel(userId: string | null, directory: DirectoryUser[]): string {
  if (!userId) return "—";
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

const SORT_KEYS = ["identifier", "title", "lead", "state", "started", "created"] as const;
type SortKey = (typeof SORT_KEYS)[number];

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <Paper withBorder p="md" data-tile>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
    </Paper>
  );
}

export function AuditsListPage() {
  const { data, isLoading, isError, forbidden, refetch } = useAudits();
  const { data: directory } = useUserDirectory();
  // status filter is URL-backed ("" = All) so it survives navigation + is shareable.
  const [filter, setFilter] = useUrlParam("status", "");
  const { can } = usePermissions();
  const [newOpen, setNewOpen] = useState(false);
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "created",
    defaultDir: "desc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const dir0 = directory ?? [];

  // The lead-auditor label resolves the directory display name — reused for BOTH the search filter
  // and the sort value so a sort/search on "lead" matches what the column shows, not the raw id.
  const visible = useMemo(() => {
    const all = data ?? [];
    const sortValue = (a: Audit, key: SortKey): string | null | undefined => {
      switch (key) {
        case "identifier":
          return a.identifier;
        case "title":
          return a.title;
        case "lead":
          return leadLabel(a.lead_auditor_user_id, dir0);
        case "state":
          return a.state;
        case "started":
          return a.started_at; // the "Started" column sorts by what it shows (nulls sort last)
        case "created":
          return a.created_at; // the hidden default order (newest-created-first)
      }
    };
    const matched = query
      ? all.filter((a) =>
          [a.identifier, a.title, leadLabel(a.lead_auditor_user_id, dir0)].some((v) =>
            v?.toLowerCase().includes(query),
          ),
        )
      : all;
    return sortRows(matched, sort, dir, sortValue);
  }, [data, directory, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <NoAccessState
          message={
            <>
              You don't have access to internal audits. They're available to roles holding{" "}
              <code>audit.read</code> (QMS Owner, Process Owner, Internal Auditor).
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="xl" py="md">
        <LoadingState label="Loading audits" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="xl" py="md">
        <Title order={3} mb="md">
          Internal Audit
        </Title>
        <ErrorState title="Couldn't load audits" onRetry={() => refetch()} />
      </Container>
    );
  }

  const all = data ?? [];
  // Active = state ≠ Closed (the spec definition).
  const isActive = (a: Audit) => a.state !== "Closed";
  // `visible` is already searched + sorted (default created desc); apply the status slice client-side.
  const rows =
    filter === "active"
      ? visible.filter(isActive)
      : filter === "closed"
        ? visible.filter((a) => !isActive(a))
        : visible;

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Internal Audit</Title>
        {can("audit.create") && <Button onClick={() => setNewOpen(true)}>＋ New audit</Button>}
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }} mb="md">
        {/* "… audits" labels: distinct from the segmented control's All/Active/Closed radio names. */}
        <Tile label="Total audits" value={all.length} />
        <Tile label="Active audits" value={all.filter(isActive).length} />
        <Tile label="Closed audits" value={all.filter((a) => !isActive(a)).length} />
      </SimpleGrid>
      <RegisterToolbar
        q={q}
        onQ={setQ}
        placeholder="Search audits…"
        count={rows.length}
        countNoun="audits"
      >
        {/* "… audits" tile labels keep these radio names collision-free. */}
        <SegmentedControl
          value={filter || "all"}
          onChange={(v) => setFilter(v === "all" ? "" : v)}
          data={[
            { value: "all", label: "All" },
            { value: "active", label: "Active" },
            { value: "closed", label: "Closed" },
          ]}
        />
      </RegisterToolbar>
      {all.length === 0 ? (
        <EmptyState message="No audits yet." />
      ) : rows.length === 0 ? (
        <EmptyState message="No audits match your filters." />
      ) : (
        <Table striped highlightOnHover mt="md">
          <Table.Thead>
            <Table.Tr>
              <SortableTh
                label="Audit"
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
                label="Lead auditor"
                sortKey="lead"
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
                label="Started"
                sortKey="started"
                sort={sort}
                dir={dir}
                onSort={toggleSort}
                scope="col"
              />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
            {rows.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/audits/${a.id}`} data-rownav>
                    {a.identifier ?? a.id.slice(0, 8)}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{a.title ?? "—"}</Text>
                </Table.Td>
                <Table.Td>{leadLabel(a.lead_auditor_user_id, dir0)}</Table.Td>
                <Table.Td>
                  <AuditStateBadge state={a.state} />
                </Table.Td>
                {/* "—" for an unstarted audit — a created_at fallback would mislabel it as started. */}
                <Table.Td>{a.started_at ?? "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <NewAuditModal opened={newOpen} onClose={() => setNewOpen(false)} />
    </Container>
  );
}
