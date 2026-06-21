import {
  Alert,
  Anchor,
  Button,
  Container,
  Group,
  SegmentedControl,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RiskBand, RiskRow } from "../../lib/types";
import { AsOf } from "../../lib/AsOf";
import { usePermissions } from "../../app/shell/usePermissions";
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
import { useProcesses } from "../objectives/hooks";
import { RISK_BAND_LABEL, RISK_BAND_ORDER, RISK_TYPE_LABEL } from "./labels";
import { useRisks, useRiskRegisterStatus } from "./hooks";
import { RiskScorecardBand } from "./RiskScorecardBand";
import { RiskMatrix } from "./RiskMatrix";
import { RiskDetailDrawer } from "./RiskDetailDrawer";
import { NewRiskModal } from "./NewRiskModal";
import { RegisterLifecyclePanel } from "./RegisterLifecyclePanel";

const SORT_KEYS = ["rating", "band", "type"] as const;
type SortKey = (typeof SORT_KEYS)[number];

// Default sort = rating DESC (worst risks first — the most useful risk-register ordering). `band` is
// NEGATED band_rank so the table's default DESC click still surfaces danger-first (server ranks
// Critical 0 → Low 3, so -rank gives Critical 0 ≥ Low -3 → Critical first on desc; Codex P3). `type`
// alphabetical.
function sortValue(r: RiskRow, key: SortKey): string | number | null {
  switch (key) {
    case "rating":
      return r.risk_rating;
    case "band":
      return -r.band_rank;
    case "type":
      return r.type;
  }
}

function bannerFor(state: string | null): string | null {
  // ⚠ Don't instruct an action the SPA doesn't expose: the register-steward lifecycle (start-revision
  // / publish / release) is the ratified F-1 deferral — the whole cycle rides the API / a SYSTEM
  // override in v1, so the banner states the read-only fact + who reopens it, never "Start a revision"
  // (there is no such button by design; Codex P1).
  if (state === "Effective")
    return "This register is Effective (read-only) — a register steward opens the next revision to enable edits.";
  if (state === "InReview" || state === "Approved")
    return "A register revision is in review — risks are read-only until it's released.";
  if (state === "Superseded" || state === "Obsolete")
    return "This register version is no longer current — risks are read-only.";
  return null; // Draft / UnderRevision / no register yet → editable, no banner
}

export function RisksRegisterPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useRisks();
  const status = useRiskRegisterStatus();
  const sys = usePermissions();
  // "New risk" gate: register.manage at SYSTEM ‖ a first-readable-process probe (the CapaBoardPage
  // idiom). Keyed on register.manage, NEVER on process-count (an Internal Auditor holds register.read
  // + a non-empty process list but no manage → gating on count would leak the button).
  const { data: readableProcesses } = useProcesses();
  const firstProcessId = readableProcesses?.[0]?.id;
  const procPerms = usePermissions(
    firstProcessId ? { level: "PROCESS", id: firstProcessId } : undefined,
  );
  const systemCanManage = sys.can("register.manage");
  const canCreate = systemCanManage || (!!firstProcessId && procPerms.can("register.manage"));
  // The register-steward console (S-risk-5):
  //  • start-revision/publish gate on register.manage @ SYSTEM — the backend forces SYSTEM there
  //    (a bound Process-Owner can't steward the org head). NOT the first-readable-process probe.
  //  • release gates on document.release at the HEAD's EFFECTIVE scope: the backend builds a
  //    doc-resource release scope (artifact_id = head.id, …), so a SYSTEM override OR an
  //    artifact-scoped grant authorizes it. The ARTIFACT probe folds matching SYSTEM grants + scoped
  //    DENYs (the S-risk-4b effective-scope doctrine), unlike a SYSTEM-only probe (Codex P2).
  const registerDocId = status.data?.register_doc_id ?? null;
  const releasePerms = usePermissions(
    registerDocId ? { level: "ARTIFACT", id: registerDocId } : undefined,
  );
  const canRelease = releasePerms.can("document.release");

  const headState = status.data?.state ?? null;
  // null = no register yet (create bootstraps) OR status not-yet-loaded/errored → don't block (the
  // server 409s a write if the head isn't really editable). The banner only shows for a known state.
  const headEditable = headState === null || headState === "Draft" || headState === "UnderRevision";
  const banner = bannerFor(headState);

  const [band, setBand] = useUrlParam("band", "");
  const [rtype, setRtype] = useUrlParam("rtype", "");
  const { q, setQ, query } = useDebouncedSearch();
  const { sort, dir, toggleSort } = useTableSort<SortKey>({
    keys: SORT_KEYS,
    defaultSort: "rating",
    defaultDir: "desc",
  });
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const [createOpen, setCreateOpen] = useState(false);

  // Drawer state, URL-seedable via ?risk=<id>: local opens never touch the URL; a deep-link opens it.
  // The sync effect keys on the ?risk= param ALONE (not the whole search-params) and follows it
  // INCLUDING its removal — so back/forward that drops ?risk closes the drawer, while a change to
  // another param (the band/type filters) leaves a locally-opened drawer untouched (Codex P3).
  const [params, setParams] = useSearchParams();
  const riskParam = params.get("risk");
  const [selected, setSelected] = useState<string | null>(riskParam);
  useEffect(() => {
    setSelected(riskParam);
  }, [riskParam]);
  function closeDrawer() {
    setSelected(null);
    if (params.has("risk")) {
      setParams(
        (p) => {
          p.delete("risk");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = useMemo(() => data ?? [], [data]);
  const tableRows = useMemo(() => {
    const filtered = rows
      .filter((r) => band === "" || r.band === band)
      .filter((r) => rtype === "" || r.type === rtype)
      .filter((r) => !query || r.description.toLowerCase().includes(query));
    return sortRows(filtered, sort, dir, sortValue);
  }, [rows, band, rtype, query, sort, dir]);

  const selectedRow = selected ? (rows.find((r) => r.id === selected) ?? null) : null;

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Risks & opportunities
        </Title>
        <NoAccessState message="You don't have access to the Risk & Opportunity register." />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Risks & opportunities
        </Title>
        <ErrorState
          title="Couldn't load the risk register"
          message="Something went wrong. Please try again."
          onRetry={() => refetch()}
        />
      </Container>
    );
  }
  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading risks" />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Risks & opportunities</Title>
        {headEditable && canCreate && <Button onClick={() => setCreateOpen(true)}>New risk</Button>}
      </Group>

      <AsOf at={dataUpdatedAt} />
      {banner && (
        <Alert color="gray" variant="light" mt="xs">
          {banner}
        </Alert>
      )}

      <RegisterLifecyclePanel
        state={headState}
        canManage={systemCanManage}
        canRelease={canRelease}
      />

      {rows.length === 0 ? (
        <Alert color="gray" title="No risks or opportunities yet" mt="md">
          {canCreate && headEditable
            ? "Add the first risk to start the register."
            : "No risks have been recorded yet."}
        </Alert>
      ) : (
        <>
          <Group align="flex-start" mt="md" gap="lg" wrap="wrap">
            <RiskMatrix rows={rows} selected={selectedRow} />
            <RiskScorecardBand rows={rows} />
          </Group>

          <RegisterToolbar
            q={q}
            onQ={setQ}
            placeholder="Search risks…"
            count={tableRows.length}
            countNoun="risks"
          >
            <SegmentedControl
              value={band}
              onChange={setBand}
              aria-label="Filter by band"
              data={[
                { value: "", label: "All" },
                ...RISK_BAND_ORDER.map((b: RiskBand) => ({
                  value: b,
                  label: RISK_BAND_LABEL[b],
                })),
              ]}
            />
            <SegmentedControl
              value={rtype}
              onChange={setRtype}
              aria-label="Filter by type"
              data={[
                { value: "", label: "All" },
                { value: "risk", label: "Risks" },
                { value: "opportunity", label: "Opportunities" },
              ]}
            />
          </RegisterToolbar>

          {tableRows.length === 0 ? (
            <Alert color="gray" title="No risks match your filters." mt="md">
              Try clearing the search or the band/type filter.
            </Alert>
          ) : (
            <Table striped highlightOnHover mt="md">
              <Table.Thead>
                <Table.Tr>
                  <SortableTh
                    label="Type"
                    sortKey="type"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <Table.Th scope="col">Risk / opportunity</Table.Th>
                  <SortableTh
                    label="Score"
                    sortKey="rating"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <SortableTh
                    label="Band"
                    sortKey="band"
                    sort={sort}
                    dir={dir}
                    onSort={toggleSort}
                    scope="col"
                  />
                  <Table.Th scope="col">Treatment</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
                {tableRows.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      <Text size="sm">{RISK_TYPE_LABEL[r.type]}</Text>
                    </Table.Td>
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
                      {r.likelihood} × {r.severity} = {r.risk_rating}
                    </Table.Td>
                    <Table.Td>
                      <StatusBadge tone={r.band_tone} label={RISK_BAND_LABEL[r.band]} kind="Band" />
                    </Table.Td>
                    <Table.Td>
                      {r.linked_capa_id ? (
                        <StatusBadge tone="info" label="CAPA raised" kind="Treatment" />
                      ) : r.treatment ? (
                        <StatusBadge tone="success" label="Treated" kind="Treatment" />
                      ) : (
                        <Text size="sm" c="dimmed">
                          —
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
        <NewRiskModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            setSelected(id);
          }}
          requireProcess={!systemCanManage}
        />
      )}
      <RiskDetailDrawer riskId={selected} onClose={closeDrawer} headEditable={headEditable} />
    </Container>
  );
}
