import {
  Alert,
  Anchor,
  Button,
  Container,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { InterestedParty, InterestedPartyInfluence } from "../../lib/types";
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
  INFLUENCE_GLYPH,
  INFLUENCE_LABEL,
  INFLUENCE_TONE,
  PARTY_TYPE_SINGULAR,
  PARTY_TYPE_TONE,
  STATUS_LABEL,
  STATUS_TONE,
} from "./labels";
import { PARTY_TYPE_ORDER } from "./board";
import { useInterestedParties, useInterestedPartyRegisterStatus } from "./hooks";
import { InterestedPartyTypeBoard } from "./InterestedPartyTypeBoard";
import { InterestedPartyScorecardBand } from "./InterestedPartyScorecardBand";
import { InterestedPartyDrawer } from "./InterestedPartyDrawer";
import { NewPartyModal } from "./NewPartyModal";
import { RegisterLifecyclePanel } from "./RegisterLifecyclePanel";

const SORT_KEYS = ["party_type", "influence", "status", "reviewed"] as const;
type SortKey = (typeof SORT_KEYS)[number];

// Influence is an ORDERED axis — sort by rank (low < medium < high), NOT alphabetically (which would
// give high/low/medium). Nulls sort last via sortRows (a null return).
const INFLUENCE_RANK: Record<InterestedPartyInfluence, number> = { low: 1, medium: 2, high: 3 };

function sortValue(r: InterestedParty, key: SortKey): string | number | null {
  switch (key) {
    case "party_type":
      return r.party_type;
    case "influence":
      return r.influence ? INFLUENCE_RANK[r.influence] : null;
    case "status":
      return r.status;
    case "reviewed":
      return r.last_reviewed_at;
  }
}

const PARTY_TYPE_FILTER_OPTIONS = PARTY_TYPE_ORDER.map((t) => ({
  value: t,
  label: PARTY_TYPE_SINGULAR[t],
}));

function bannerFor(state: string | null): string | null {
  // ⚠ State the read-only fact + who reopens it — never instruct an action the surface only exposes to
  // a steward (the in-app console is gated to register.manage holders; the S-context-fe copy lesson).
  if (state === "Effective")
    return "This register is Effective (read-only) — a register steward opens the next revision to enable edits.";
  if (state === "InReview" || state === "Approved")
    return "A register revision is in review — interested parties are read-only until it's released.";
  if (state === "Superseded" || state === "Obsolete")
    return "This register version is no longer current — interested parties are read-only.";
  return null; // Draft / UnderRevision / no register yet → editable, no banner
}

export function InterestedPartiesRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useInterestedParties();
  const status = useInterestedPartyRegisterStatus();

  // Clause 4.2 is ORG-LEVEL — every gate is the server-computed capability on GET
  // /interested-parties/register (the faithful multi-axis answer), never a single-axis FE
  // /me/permissions probe. canManage gates the "New party" + edit affordances + the steward
  // start-revision/publish; canRelease gates release.
  const canManage = status.data?.can_manage ?? false;
  const canRelease = status.data?.can_release ?? false;

  const headState = status.data?.state ?? null;
  // null = no register yet (create bootstraps) OR status not-yet-loaded → don't block (the server 409s
  // a write if the head isn't really editable). The banner only shows for a known non-editable state.
  const headEditable = headState === null || headState === "Draft" || headState === "UnderRevision";
  const banner = bannerFor(headState);

  const [pt, setPt] = useUrlParam("party_type", "");
  const [inf, setInf] = useUrlParam("influence", "");
  const [st, setSt] = useUrlParam("status", "");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "party_type",
    defaultDir: "asc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const [createOpen, setCreateOpen] = useState(false);

  // Drawer state, URL-seedable via ?party=<id>: local opens never touch the URL; a deep-link opens it.
  // The sync effect keys on the ?party= param ALONE and follows it INCLUDING its removal — so
  // back/forward that drops ?party closes the drawer, while a change to another param (the filters)
  // leaves a locally-opened drawer untouched (the S-context-fe Codex P3 lesson).
  const [params, setParams] = useSearchParams();
  const partyParam = params.get("party");
  const [selected, setSelected] = useState<string | null>(partyParam);
  useEffect(() => {
    setSelected(partyParam);
  }, [partyParam]);
  function closeDrawer() {
    setSelected(null);
    if (params.has("party")) {
      setParams(
        (p) => {
          p.delete("party");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = useMemo(() => data ?? [], [data]);
  const tableRows = useMemo(() => {
    const filtered = rows
      .filter((r) => pt === "" || r.party_type === pt)
      .filter((r) =>
        inf === "" ? true : inf === "unspecified" ? r.influence === null : r.influence === inf,
      )
      .filter((r) => st === "" || r.status === st)
      .filter(
        (r) =>
          !query ||
          r.party_name.toLowerCase().includes(query) ||
          r.needs_expectations.toLowerCase().includes(query),
      );
    return sortRows(filtered, sort, dir, sortValue);
  }, [rows, pt, inf, st, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Interested parties
        </Title>
        <NoAccessState message="You don't have access to the Interested Parties register." />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Interested parties
        </Title>
        <ErrorState
          title="Couldn't load the interested-parties register"
          message="Something went wrong. Please try again."
          onRetry={() => refetch()}
        />
      </Container>
    );
  }
  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading interested parties" />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Interested parties</Title>
        {headEditable && canManage && (
          <Button onClick={() => setCreateOpen(true)}>New party</Button>
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
        <Alert color="gray" title="No interested parties yet" mt="md">
          {canManage && headEditable
            ? "Add the first interested party to start the register."
            : "No interested parties have been recorded yet."}
        </Alert>
      ) : (
        <>
          <Stack mt="md" gap="sm">
            <InterestedPartyTypeBoard rows={rows} selectedId={selected} onSelect={setSelected} />
            <InterestedPartyScorecardBand rows={rows} />
          </Stack>

          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search parties…"
            count={tableRows.length}
            countNoun="parties"
          >
            <Select
              aria-label="Filter by party type"
              placeholder="All party types"
              clearable
              value={pt || null}
              onChange={(v) => setPt(v ?? "")}
              data={PARTY_TYPE_FILTER_OPTIONS}
              comboboxProps={{ keepMounted: false }}
              w={180}
            />
            <SegmentedControl
              value={inf}
              onChange={setInf}
              aria-label="Filter by influence"
              data={[
                { value: "", label: "All" },
                { value: "high", label: "High" },
                { value: "medium", label: "Medium" },
                { value: "low", label: "Low" },
                { value: "unspecified", label: "Unspecified" },
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
            <Alert color="gray" title="No parties match your filters." mt="md">
              Try clearing the search or the party-type / influence / status filter.
            </Alert>
          ) : (
            <Table striped highlightOnHover mt="md">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Party</Table.Th>
                  <SortableTh
                    label="Type"
                    sortKey="party_type"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Influence"
                    sortKey="influence"
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
                        <Text lineClamp={1}>{r.party_name}</Text>
                      </Anchor>
                      <Text size="xs" c="dimmed" lineClamp={1}>
                        {r.needs_expectations}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <StatusBadge
                        tone={PARTY_TYPE_TONE[r.party_type]}
                        label={PARTY_TYPE_SINGULAR[r.party_type]}
                        kind="Party type"
                      />
                    </Table.Td>
                    <Table.Td>
                      {r.influence ? (
                        <StatusBadge
                          tone={INFLUENCE_TONE[r.influence]}
                          glyph={INFLUENCE_GLYPH[r.influence]}
                          label={INFLUENCE_LABEL[r.influence]}
                          kind="Influence"
                        />
                      ) : (
                        <Text size="sm" c="dimmed">
                          Unspecified
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
        <NewPartyModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            setSelected(id);
          }}
        />
      )}
      <InterestedPartyDrawer
        partyId={selected}
        onClose={closeDrawer}
        headEditable={headEditable}
        canManage={canManage}
      />
    </Container>
  );
}
