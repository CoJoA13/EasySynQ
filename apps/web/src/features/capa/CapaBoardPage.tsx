import {
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Container,
  Group,
  ScrollArea,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { TruncationNotice } from "../../app/shell/TruncationNotice";
import { RegisterFilterBar } from "../registers/RegisterFilterBar";
import type { RegisterFilterState } from "../registers/registerFilters";
import { RegisterPageHeader } from "../../lib/RegisterPageHeader";
import { readSearchParamState } from "../../lib/effectiveView";
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import type { Capa, CapaCloseState, CapaSource, NcSeverity } from "../../lib/types";
import { TONE_GLYPH, type Tone } from "../../lib/status";
import { CapaCard } from "./CapaCard";
import { CapaDrawer } from "./CapaDrawer";
import {
  CAPA_COLUMNS,
  CLOSE_STATE_LABEL,
  columnKeyFor,
  SEVERITY_LABEL,
  SOURCE_LABEL,
} from "./columns";
import { SeverityBadge } from "./SeverityBadge";
import { useProcesses } from "../objectives/hooks";
import { useCapas } from "./hooks";
import { RaiseCapaModal } from "./RaiseCapaModal";

const TERMINAL: CapaCloseState[] = ["Closed", "Rejected"];

export function CapaBoardPage() {
  const [registerFilters, setRegisterFilters] = useState<RegisterFilterState>({});
  const { data, isLoading, isError, forbidden, truncated, dataUpdatedAt, refetch } =
    useCapas(registerFilters);
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  const [view, setView] = useState<"board" | "list">("board");
  const [source, setSource] = useState<CapaSource | "">("");
  const [severity, setSeverity] = useState<NcSeverity | "">("");
  const [state, setState] = useState<CapaCloseState | "">("");
  // Drawer state is local, but URL-seedable: ?capa=<id> deep-links a specific CAPA's drawer open (so
  // other surfaces — e.g. Complaints' "View CAPA" — can link to it). Mirrors the S-web-4 ?from=&to=
  // redline pattern. Card/list/raise opens stay local-only (URL untouched) to keep the board unchanged.
  const [params, setParams] = useSearchParams();
  const capaSelectorState = readSearchParamState(params, "capa");
  const selectedCapaParam = capaSelectorState.kind === "unique" ? capaSelectorState.value : null;
  const [selected, setSelected] = useState<string | null>(selectedCapaParam);
  const [raiseOpen, setRaiseOpen] = useState(false);
  const perms = usePermissions();
  // The Raise affordance must reach a bound Process-Owner, who holds capa.create only at their owned
  // process(es) — never at SYSTEM. Probe capa.create at the caller's first readable process (the
  // owner-assignment binding mints process.read + capa.create over the SAME bound set, so any readable
  // process the owner can read is one they can raise in). SYSTEM-grant holders short-circuit via `perms`.
  // The server's PROCESS-scoped POST /capas enforce stays the true boundary (a 403 surfaces calmly).
  const { data: readableProcesses } = useProcesses();
  const firstProcessId = readableProcesses?.[0]?.id;
  const processPerms = usePermissions(
    firstProcessId ? { level: "PROCESS", id: firstProcessId } : undefined,
  );
  const systemCanCreate = perms.can("capa.create");
  const canRaiseCapa = systemCanCreate || (!!firstProcessId && processPerms.can("capa.create"));

  // Keep URL-seeded ?capa=<id> selection in sync, including removal, without overwriting local opens
  // when another search param changes.
  useEffect(() => {
    setSelected(selectedCapaParam);
  }, [capaSelectorState.kind, selectedCapaParam]);

  function closeDrawer() {
    setSelected(null);
    // Only touch the URL when a deep-link param is actually present, so the common (local) open/close
    // path leaves history untouched. Replace, so closing doesn't leave a back-step that re-opens it.
    if (params.has("capa")) {
      setParams(
        (p) => {
          p.delete("capa");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = data ?? [];
  const filtered = useMemo(
    () =>
      rows.filter(
        (c) =>
          (source === "" || c.source === source) &&
          (severity === "" || c.severity === severity) &&
          (state === "" || c.close_state === state),
      ),
    [rows, source, severity, state],
  );

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <RegisterPageHeader title="Nonconformity and CAPA" />
        <NoAccessState
          message={
            <>
              You don't have access to the CAPA board. It's available to the Quality Manager,
              Process Owner and Internal Auditor roles.
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <LoadingState label="Loading CAPAs" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="md" py="md">
        <RegisterPageHeader title="Nonconformity and CAPA" />
        <ErrorState title="Couldn't load CAPAs" onRetry={() => refetch()} />
      </Container>
    );
  }

  // ONE population for all four tiles. `openCount` always excluded terminal CAPAs and `overdue` is
  // non-terminal by construction (the server forces the flag false for Closed/Rejected), but the
  // histograms counted every loaded row — so the row read "Open CAPAs 5" beside a severity
  // breakdown summing to 7. On a mature board that is not a rounding oddity: an org with 1 open and
  // 40 closed CAPAs showed "Open CAPAs 1" next to "Critical · 12", and severity is the axis an
  // operator triages on. Scoping the histograms to the live rows makes the four tiles arithmetic —
  // both breakdowns now total `openCount`, which `CapaBoardPage.test.tsx` asserts directly.
  const live = rows.filter((c) => !TERMINAL.includes(c.close_state));
  const openCount = live.length;
  // Server-computed (api/capa.py::_capa): already false for Closed/Rejected and evaluated in the
  // org's timezone, so this is a plain count and never re-derives the date comparison client-side.
  const overdueCount = rows.filter((c) => c.overdue).length;
  const overdueTone: Tone = overdueCount > 0 ? "danger" : "success";
  const bySeverity = (Object.keys(SEVERITY_LABEL) as NcSeverity[])
    .map((s) => ({ severity: s, n: live.filter((c) => c.severity === s).length }))
    .filter((x) => x.n > 0);
  const bySource = (Object.keys(SOURCE_LABEL) as CapaSource[])
    .map((s) => ({ source: s, n: live.filter((c) => c.source === s).length }))
    .filter((x) => x.n > 0);

  return (
    <Container size="xl" py="md">
      <RegisterPageHeader
        title="Nonconformity and CAPA"
        actions={
          <Group gap="sm">
            {canRaiseCapa && <Button onClick={() => setRaiseOpen(true)}>Raise CAPA</Button>}
            <SegmentedControl
              value={view}
              onChange={(v) => setView(v as "board" | "list")}
              data={[
                { value: "board", label: "Board" },
                { value: "list", label: "List" },
              ]}
            />
          </Group>
        }
        updatedAt={dataUpdatedAt}
      />
      {/* Only the date window. This page already has client-side Severity / Stage / Source
          selects that narrow the loaded rows; duplicating them here would render two controls
          with the same label. The date window is the one facet no register had, and the only one
          that reaches entries older than the server's scan window. */}
      <RegisterFilterBar value={registerFilters} onChange={setRegisterFilters} />
      <TruncationNotice truncated={truncated} noun="CAPAs" />
      {/* Four tiles, not two. Two cards spent the whole page width on one digit and three pills —
          477px of card each at 1280 and 638px at 1600, with every mark in the leftmost ~60px — so
          the row read as a blank band with a seam in it. Severity and overdue are the two aggregates
          an operator actually opens this board for, and both come from rows ALREADY loaded: no new
          endpoint, no second query. The `{ base: 1, sm: 2, lg: 4 }` ramp is RunSummaryTiles'. */}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} mb="md">
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Open CAPAs
          </Text>
          <Text fz="xl" fw={700}>
            {openCount}
          </Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Overdue
          </Text>
          {/* The glyph rides the VALUE, not the caption. Sharing a Group with the caption centred a
              13px label against a 16px glyph and dropped this tile's caption ~2px below the other
              three — a visible stagger across a row whose whole point is that things line up. */}
          <Group gap={6} align="center" wrap="nowrap">
            <Text fz="xl" fw={700}>
              {overdueCount}
            </Text>
            {/* The non-colour channel (DP-7): the caption and the count still carry the meaning
                with the colour removed. */}
            <Text aria-hidden size="sm" c={`var(--es-${overdueTone}-text)`}>
              {TONE_GLYPH[overdueTone]}
            </Text>
          </Group>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" mb={4}>
            By severity
          </Text>
          <Group gap="xs">
            {bySeverity.map((x) => (
              <SeverityBadge key={x.severity} severity={x.severity} count={x.n} />
            ))}
          </Group>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" mb={4}>
            By source
          </Text>
          <Group gap="xs">
            {bySource.map((x) => (
              <Badge key={x.source} variant="light" color="gray">
                {SOURCE_LABEL[x.source]} · {x.n}
              </Badge>
            ))}
          </Group>
        </Card>
      </SimpleGrid>

      {/* `grow` because these three selects are a BLOCK, not a toolbar: at their natural width the
          row is a fixed ~648px while the summary grid above it tracks the viewport, so the right
          edges part company by 44px at 1000 and 324px at 1280 — the mismatch grows with the window.
          `preventGrowOverflow={false}` lets a select exceed its 1/3 share rather than truncating a
          long option label. Measured in e2e/capa-board.spec.ts. */}
      <Group mb="md" gap="sm" grow preventGrowOverflow={false}>
        <Select
          aria-label="Source"
          placeholder="All sources"
          clearable
          value={source || null}
          onChange={(v) => setSource((v as CapaSource) ?? "")}
          data={Object.entries(SOURCE_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Select
          aria-label="Severity"
          placeholder="All severities"
          clearable
          value={severity || null}
          onChange={(v) => setSeverity((v as NcSeverity) ?? "")}
          data={Object.entries(SEVERITY_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Select
          aria-label="State"
          placeholder="All states"
          clearable
          value={state || null}
          onChange={(v) => setState((v as CapaCloseState) ?? "")}
          data={(
            [
              "Raised",
              "Containment",
              "RootCause",
              "ActionPlan",
              "Implement",
              "Verify",
              "Closed",
              "Rejected",
            ] as CapaCloseState[]
          ).map((s) => ({ value: s, label: CLOSE_STATE_LABEL[s] }))}
        />
      </Group>

      {filtered.length === 0 ? (
        <EmptyState message="No CAPAs match." />
      ) : view === "board" ? (
        <ScrollArea>
          <Group align="flex-start" wrap="nowrap" gap="md">
            {CAPA_COLUMNS.map((col) => {
              const cards = filtered.filter((c) => columnKeyFor(c.close_state) === col.key);
              return (
                <Box key={col.key} role="group" aria-label={col.label} miw={260} w={260}>
                  <Group justify="space-between" mb="xs">
                    <Text fw={600} size="sm">
                      {col.label}
                    </Text>
                    <Badge variant="light" color="gray">
                      {cards.length}
                    </Badge>
                  </Group>
                  <Stack gap="xs">
                    {cards.map((c) => (
                      <CapaCard key={c.id} capa={c} onOpen={setSelected} />
                    ))}
                  </Stack>
                </Box>
              );
            })}
          </Group>
        </ScrollArea>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Source</Table.Th>
              <Table.Th>State</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
            {filtered.map((c: Capa) => {
              const identifier = c.identifier ?? "—";
              const title = c.title ?? "(untitled)";
              return (
                <Table.Tr key={c.id}>
                  <Table.Td>
                    <Anchor
                      component="button"
                      type="button"
                      data-rownav
                      onClick={() => setSelected(c.id)}
                      aria-label={`Open CAPA ${identifier}: ${title}`}
                    >
                      {identifier}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{title}</Table.Td>
                  <Table.Td>{SEVERITY_LABEL[c.severity]}</Table.Td>
                  <Table.Td>{SOURCE_LABEL[c.source]}</Table.Td>
                  <Table.Td>{CLOSE_STATE_LABEL[c.close_state]}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      <CapaDrawer capaId={selected} onClose={closeDrawer} />
      {/* Conditionally mounted so close unmounts + resets the form (the RaiseInitiativeModal
          precedent) — a picked-then-cancelled process must not persist into the next raise. */}
      {raiseOpen && (
        <RaiseCapaModal
          opened
          onClose={() => setRaiseOpen(false)}
          onCreated={(id) => setSelected(id)}
          requireProcess={!systemCanCreate}
        />
      )}
    </Container>
  );
}
