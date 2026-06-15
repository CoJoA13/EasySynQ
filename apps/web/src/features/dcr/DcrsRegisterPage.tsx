import {
  Alert,
  Anchor,
  Button,
  Container,
  Group,
  Loader,
  Select,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Dcr, DcrChangeType, DcrReasonClass, DcrState } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { RegisterToolbar, SortableTh, SubjectCell } from "../../lib/RegisterToolbar";
import {
  sortRows,
  useDebouncedSearch,
  useTableSort,
  useUrlParam,
} from "../../lib/registerControls";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { DcrDrawer } from "./DcrDrawer";
import { DcrStateBadge } from "./DcrStateBadge";
import { CHANGE_TYPE_LABEL, REASON_LABEL, SIGNIFICANCE_LABEL } from "./labels";
import { useDcrs } from "./hooks";
import { RaiseDcrModal } from "./RaiseDcrModal";

const STATES: DcrState[] = [
  "Open",
  "Assessed",
  "Routed",
  "InApproval",
  "Approved",
  "Implemented",
  "Closed",
  "Cancelled",
  "Rejected",
];

const SORT_KEYS = [
  "identifier",
  "type",
  "significance",
  "reason",
  "target",
  "state",
  "created",
] as const;
type SortKey = (typeof SORT_KEYS)[number];

function sortValue(d: Dcr, key: SortKey): string | null | undefined {
  switch (key) {
    case "identifier":
      return d.identifier;
    case "type":
      return CHANGE_TYPE_LABEL[d.change_type];
    case "significance":
      return SIGNIFICANCE_LABEL[d.change_significance];
    case "reason":
      return REASON_LABEL[d.reason_class];
    case "target":
      return d.target_identifier ?? d.target_title ?? "";
    case "state":
      return d.state;
    case "created":
      return d.created_at;
  }
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function DcrsRegisterPage() {
  const { data, isLoading, isError, forbidden } = useDcrs();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(() => params.get("dcr"));
  // URL-backed enum filters (critique #5): they survive navigation + are shareable. Distinct keys
  // (state / ctype / reason) — none collide with the `dcr` drawer deep-link seam below.
  const [state, setState] = useUrlParam("state");
  const [changeType, setChangeType] = useUrlParam("ctype");
  const [reason, setReason] = useUrlParam("reason");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "created",
    defaultDir: "desc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const { can } = usePermissions();
  const [raising, setRaising] = useState(false);

  // Open the drawer for ?dcr=<id> on mount + whenever the param changes (a deep-link while mounted).
  // Guarded on a non-null id so clearing the param on close never re-opens the drawer.
  useEffect(() => {
    const dcr = params.get("dcr");
    if (dcr) setSelected(dcr);
  }, [params]);

  function closeDrawer() {
    setSelected(null);
    // Only touch the URL when a deep-link param is actually present, so the common (local) open/close
    // path leaves history untouched. Replace, so closing doesn't leave a back-step that re-opens it.
    if (params.has("dcr")) {
      setParams(
        (p) => {
          p.delete("dcr");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = data ?? [];
  const visible = useMemo(() => {
    const matched = rows.filter(
      (d) =>
        (state === "" || d.state === state) &&
        (changeType === "" || d.change_type === changeType) &&
        (reason === "" || d.reason_class === reason) &&
        (query === "" ||
          [d.identifier, d.target_identifier, d.target_title, d.reason_text].some((v) =>
            v?.toLowerCase().includes(query),
          )),
    );
    return sortRows(matched, sort, dir, sortValue);
  }, [rows, state, changeType, reason, query, sort, dir]);

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the change-request register. It's available to roles holding the
          change-request read permission.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="red" title="Couldn't load change requests">
          Please try again.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Change requests</Title>
        {can("changeRequest.create") && <Button onClick={() => setRaising(true)}>Raise DCR</Button>}
      </Group>

      {rows.length === 0 ? (
        <Text c="dimmed">No change requests yet.</Text>
      ) : (
        <>
          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search change requests…"
            count={visible.length}
            countNoun="change requests"
          >
            <Select
              aria-label="State"
              placeholder="All states"
              clearable
              value={state || null}
              onChange={(v) => setState((v as DcrState) ?? "")}
              data={STATES.map((s) => ({ value: s, label: s }))}
            />
            <Select
              aria-label="Change type"
              placeholder="All change types"
              clearable
              value={changeType || null}
              onChange={(v) => setChangeType((v as DcrChangeType) ?? "")}
              data={(Object.entries(CHANGE_TYPE_LABEL) as [DcrChangeType, string][]).map(
                ([value, label]) => ({ value, label }),
              )}
            />
            <Select
              aria-label="Reason"
              placeholder="All reasons"
              clearable
              value={reason || null}
              onChange={(v) => setReason((v as DcrReasonClass) ?? "")}
              data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(
                ([value, label]) => ({ value, label }),
              )}
            />
          </RegisterToolbar>

          {visible.length === 0 ? (
            <Text c="dimmed" mt="md">
              No change requests match your filters.
            </Text>
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
                    label="Type"
                    sortKey="type"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Significance"
                    sortKey="significance"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Reason"
                    sortKey="reason"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Target"
                    sortKey="target"
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
                    label="Created"
                    sortKey="created"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {visible.map((d) => (
                  <Table.Tr key={d.id}>
                    <Table.Td>
                      <Anchor
                        component="button"
                        type="button"
                        data-rownav
                        onClick={() => setSelected(d.id)}
                      >
                        {d.identifier}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>{CHANGE_TYPE_LABEL[d.change_type]}</Table.Td>
                    <Table.Td>{SIGNIFICANCE_LABEL[d.change_significance]}</Table.Td>
                    <Table.Td>{REASON_LABEL[d.reason_class]}</Table.Td>
                    <Table.Td>
                      <SubjectCell
                        identifier={d.target_identifier}
                        title={d.target_title}
                        fallback={d.change_type === "CREATE" ? "New document" : "—"}
                      />
                    </Table.Td>
                    <Table.Td>
                      <DcrStateBadge state={d.state} />
                    </Table.Td>
                    <Table.Td>{formatDate(d.created_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}

      <DcrDrawer dcrId={selected} onClose={closeDrawer} />
      {raising && (
        <RaiseDcrModal onClose={() => setRaising(false)} onCreated={(id) => setSelected(id)} />
      )}
    </Container>
  );
}
