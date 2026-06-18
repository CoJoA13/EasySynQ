import { Alert, Anchor, Box, Button, Container, Group, Table, Text, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { AsOf } from "../../lib/AsOf";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { RegisterToolbar, SortableTh } from "../../lib/RegisterToolbar";
import { sortRows, useDebouncedSearch, useTableSort } from "../../lib/registerControls";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { MgmtReview, MgmtReviewCloseState } from "../../lib/types";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReviews } from "./hooks";
import { NewManagementReviewModal } from "./NewManagementReviewModal";

const SORT_KEYS = ["identifier", "title", "period", "review_date", "status"] as const;
type SortKey = (typeof SORT_KEYS)[number];

// close_state → label + canonical status tone (feature-local; only Tone + glyphs are shared,
// S-statusbadge-2). Closed → success ✓ (closed-ok / done); ActionsTracked → info ● (open/active,
// actions still being tracked, not yet closed).
const CLOSE_STATE_META: Record<MgmtReviewCloseState, { label: string; tone: Tone }> = {
  Closed: { label: "Closed", tone: "success" },
  ActionsTracked: { label: "Actions tracked", tone: "info" },
};

// The Status column shows the close_state ("Closed"/"Actions tracked") once a review is released; sort
// on that token so the two open-action reviews cluster apart from the closed ones.
function sortValue(mr: MgmtReview, key: SortKey): string | null | undefined {
  switch (key) {
    case "identifier":
      return mr.identifier;
    case "title":
      return mr.title;
    case "period":
      return mr.period_label;
    case "review_date":
      return mr.review_date;
    case "status":
      return mr.close_state;
  }
}

export function ManagementReviewsRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useMgmtReviews();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const { q, setQ, query } = useDebouncedSearch();
  // Default sort = identifier asc, matching the current server order (so the default view is unchanged).
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "identifier",
    defaultDir: "asc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();

  const rows = data?.data;
  const visible = useMemo(() => {
    const all = rows ?? [];
    const matched = query
      ? all.filter((mr) =>
          [mr.identifier, mr.title, mr.period_label].some((v) => v?.toLowerCase().includes(query)),
        )
      : all;
    return sortRows(matched, sort, dir, sortValue);
  }, [rows, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Management reviews
        </Title>
        <NoAccessState message="You don't have access to Management Reviews. It's available to the Quality Manager." />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Management reviews
        </Title>
        <ErrorState title="Couldn't load management reviews" onRetry={() => refetch()} />
      </Container>
    );
  }
  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading management reviews" />
      </Container>
    );
  }
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Management reviews</Title>
        {can("mgmtReview.create") && (
          <Button onClick={() => setCreateOpen(true)}>New management review</Button>
        )}
      </Group>
      <AsOf at={dataUpdatedAt} />
      {data.data.length === 0 ? (
        <Alert color="gray" title="No management reviews yet" mt="md">
          {can("mgmtReview.create")
            ? "Convene the first management review to record clause 9.3 minutes."
            : "No management reviews have been convened yet."}
        </Alert>
      ) : (
        <>
          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search reviews…"
            count={visible.length}
            countNoun="reviews"
          />
          {visible.length === 0 ? (
            <Box mt="md">
              <EmptyState message="No management reviews match your search." />
            </Box>
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
                    label="Review"
                    sortKey="title"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Period"
                    sortKey="period"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Review date"
                    sortKey="review_date"
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
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {visible.map((mr) => (
                  <Table.Tr key={mr.id}>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Anchor component={Link} to={`/management-reviews/${mr.id}`} data-rownav>
                          {mr.identifier}
                        </Anchor>
                        {/* The steady state (Effective) stays unmarked; every other state gets the chip. */}
                        {mr.current_state !== "Effective" && (
                          <StateBadge state={mr.current_state} size="xs" />
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Text lineClamp={1}>{mr.title}</Text>
                    </Table.Td>
                    <Table.Td>{mr.period_label ?? "—"}</Table.Td>
                    <Table.Td>{mr.review_date ?? "—"}</Table.Td>
                    <Table.Td>
                      {mr.close_state ? (
                        <StatusBadge
                          tone={CLOSE_STATE_META[mr.close_state].tone}
                          label={CLOSE_STATE_META[mr.close_state].label}
                          kind="Status"
                        />
                      ) : (
                        "—"
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
        <NewManagementReviewModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            navigate(`/management-reviews/${id}`);
          }}
        />
      )}
    </Container>
  );
}
