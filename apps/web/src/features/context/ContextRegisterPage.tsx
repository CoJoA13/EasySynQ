import {
  Alert,
  Anchor,
  Button,
  Container,
  Group,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ContextIssue } from "../../lib/types";
import { AsOf } from "../../lib/AsOf";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { StatusBadge } from "../../lib/StatusBadge";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import {
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "../../lib/registerControls";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import {
  CATEGORY_LABEL,
  CATEGORY_TONE,
  CLASSIFICATION_GLYPH,
  CLASSIFICATION_LABEL,
  CLASSIFICATION_TONE,
  STATUS_LABEL,
  STATUS_TONE,
} from "./labels";
import { useContextIssues, useContextRegisterStatus } from "./hooks";
import { ContextSwotBoard } from "./ContextSwotBoard";
import { ContextScorecardBand } from "./ContextScorecardBand";
import { ContextIssueDrawer } from "./ContextIssueDrawer";
import { NewIssueModal } from "./NewIssueModal";
import { RegisterLifecyclePanel } from "./RegisterLifecyclePanel";

const SORT_KEYS = ["classification", "category", "status", "reviewed"] as const;
type SortKey = (typeof SORT_KEYS)[number];

// Nulls sort LAST in sortRows — an uncategorized row / never-reviewed row trails the categorized /
// reviewed ones, regardless of direction.
function sortValue(r: ContextIssue, key: SortKey): string | null {
  switch (key) {
    case "classification":
      return r.classification;
    case "category":
      return r.category;
    case "status":
      return r.status;
    case "reviewed":
      return r.last_reviewed_at;
  }
}

function bannerFor(state: string | null): string | null {
  // ⚠ State the read-only fact + who reopens it — never instruct an action the surface only exposes to
  // a steward (the in-app console is gated to register.manage holders; Codex P1 copy lesson).
  if (state === "Effective")
    return "This register is Effective (read-only) — a register steward opens the next revision to enable edits.";
  if (state === "InReview" || state === "Approved")
    return "A register revision is in review — context issues are read-only until it's released.";
  if (state === "Superseded" || state === "Obsolete")
    return "This register version is no longer current — context issues are read-only.";
  return null; // Draft / UnderRevision / no register yet → editable, no banner
}

export function ContextRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useContextIssues();
  const status = useContextRegisterStatus();

  // Context is ORG-LEVEL — every gate is the server-computed capability on GET /context/register (the
  // faithful multi-axis answer), never a single-axis FE /me/permissions probe. canManage gates the
  // "New issue" + edit affordances + the steward start-revision/publish; canRelease gates release.
  const canManage = status.data?.can_manage ?? false;
  const canRelease = status.data?.can_release ?? false;

  const headState = status.data?.state ?? null;
  // null = no register yet (create bootstraps) OR status not-yet-loaded → don't block (the server 409s
  // a write if the head isn't really editable). The banner only shows for a known non-editable state.
  const headEditable = headState === null || headState === "Draft" || headState === "UnderRevision";
  const banner = bannerFor(headState);

  const [cls, setCls] = useUrlParam("classification", "");
  const [cat, setCat] = useUrlParam("category", "");
  const [st, setSt] = useUrlParam("status", "");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "classification",
    defaultDir: "asc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const [createOpen, setCreateOpen] = useState(false);

  // Drawer state, URL-seedable via ?issue=<id>: local opens never touch the URL; a deep-link opens it.
  // The sync effect keys on the ?issue= param ALONE and follows it INCLUDING its removal — so
  // back/forward that drops ?issue closes the drawer, while a change to another param (the filters)
  // leaves a locally-opened drawer untouched (Codex P3).
  const [params, setParams] = useSearchParams();
  const issueParam = params.get("issue");
  const [selected, setSelected] = useState<string | null>(issueParam);
  useEffect(() => {
    setSelected(issueParam);
  }, [issueParam]);
  function closeDrawer() {
    setSelected(null);
    if (params.has("issue")) {
      setParams(
        (p) => {
          p.delete("issue");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = useMemo(() => data ?? [], [data]);
  const tableRows = useMemo(() => {
    const filtered = rows
      .filter((r) => cls === "" || r.classification === cls)
      .filter((r) =>
        cat === "" ? true : cat === "uncategorized" ? r.category === null : r.category === cat,
      )
      .filter((r) => st === "" || r.status === st)
      .filter((r) => !query || r.description.toLowerCase().includes(query));
    return sortRows(filtered, sort, dir, sortValue);
  }, [rows, cls, cat, st, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Context of the organization
        </Title>
        <NoAccessState message="You don't have access to the Context register." />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Context of the organization
        </Title>
        <ErrorState
          title="Couldn't load the context register"
          message="Something went wrong. Please try again."
          onRetry={() => refetch()}
        />
      </Container>
    );
  }
  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading context issues" />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Context of the organization</Title>
        {headEditable && canManage && (
          <Button onClick={() => setCreateOpen(true)}>New issue</Button>
        )}
      </Group>

      <AsOf at={dataUpdatedAt} />
      {banner && (
        <Alert color="gray" variant="light" mt="xs">
          {banner}
        </Alert>
      )}

      <RegisterLifecyclePanel state={headState} canManage={canManage} canRelease={canRelease} />

      {rows.length === 0 ? (
        <Alert color="gray" title="No context issues yet" mt="md">
          {canManage && headEditable
            ? "Add the first internal or external issue to start the register."
            : "No context issues have been recorded yet."}
        </Alert>
      ) : (
        <>
          <Stack mt="md" gap="sm">
            <ContextSwotBoard rows={rows} selectedId={selected} onSelect={setSelected} />
            <ContextScorecardBand rows={rows} />
          </Stack>

          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search issues…"
            count={tableRows.length}
            countNoun="issues"
          >
            <SegmentedControl
              value={cls}
              onChange={setCls}
              aria-label="Filter by classification"
              data={[
                { value: "", label: "All" },
                { value: "internal", label: "Internal" },
                { value: "external", label: "External" },
              ]}
            />
            <SegmentedControl
              value={cat}
              onChange={setCat}
              aria-label="Filter by category"
              data={[
                { value: "", label: "All" },
                { value: "strength", label: "Strength" },
                { value: "weakness", label: "Weakness" },
                { value: "opportunity", label: "Opportunity" },
                { value: "threat", label: "Threat" },
                { value: "uncategorized", label: "Uncategorized" },
              ]}
            />
            <SegmentedControl
              value={st}
              onChange={setSt}
              aria-label="Filter by status"
              data={[
                { value: "", label: "All" },
                { value: "active", label: "Active" },
                { value: "closed", label: "Closed" },
              ]}
            />
          </RegisterToolbar>

          {tableRows.length === 0 ? (
            <Alert color="gray" title="No issues match your filters." mt="md">
              Try clearing the search or the classification / category / status filter.
            </Alert>
          ) : (
            <Table striped highlightOnHover mt="md">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Issue</Table.Th>
                  <SortableTh
                    label="Classification"
                    sortKey="classification"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Category"
                    sortKey="category"
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
                    label="Last reviewed"
                    sortKey="reviewed"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {tableRows.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      <Anchor
                        component="button"
                        type="button"
                        onClick={() => setSelected(r.id)}
                        data-rownav
                        ta="left"
                      >
                        <Text lineClamp={1}>{r.description}</Text>
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <StatusBadge
                        tone={CLASSIFICATION_TONE[r.classification]}
                        glyph={CLASSIFICATION_GLYPH[r.classification]}
                        label={CLASSIFICATION_LABEL[r.classification]}
                        kind="Classification"
                      />
                    </Table.Td>
                    <Table.Td>
                      {r.category ? (
                        <StatusBadge
                          tone={CATEGORY_TONE[r.category]}
                          label={CATEGORY_LABEL[r.category]}
                          kind="SWOT"
                        />
                      ) : (
                        <Text size="sm" c="dimmed">
                          —
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <StatusBadge
                        tone={STATUS_TONE[r.status]}
                        label={STATUS_LABEL[r.status]}
                        kind="Status"
                      />
                    </Table.Td>
                    <Table.Td>
                      {r.last_reviewed_at ? (
                        <Text size="sm">{r.last_reviewed_at.slice(0, 10)}</Text>
                      ) : (
                        <Text size="sm" c="dimmed">
                          Never
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      {createOpen && (
        <NewIssueModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            setSelected(id);
          }}
        />
      )}
      <ContextIssueDrawer
        issueId={selected}
        onClose={closeDrawer}
        headEditable={headEditable}
        canManage={canManage}
      />
    </Container>
  );
}
