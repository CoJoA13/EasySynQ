import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Container,
  Group,
  Loader,
  ScrollArea,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Capa, CapaCloseState, CapaSource, NcSeverity } from "../../lib/types";
import { CapaCard } from "./CapaCard";
import { CapaDrawer } from "./CapaDrawer";
import { CAPA_COLUMNS, columnKeyFor, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { useCapas } from "./hooks";
import { RaiseCapaModal } from "./RaiseCapaModal";

const TERMINAL: CapaCloseState[] = ["Closed", "Rejected"];

export function CapaBoardPage() {
  const { data, isLoading, isError, forbidden } = useCapas();
  const [view, setView] = useState<"board" | "list">("board");
  const [source, setSource] = useState<CapaSource | "">("");
  const [severity, setSeverity] = useState<NcSeverity | "">("");
  const [state, setState] = useState<CapaCloseState | "">("");
  // Drawer state is local, but URL-seedable: ?capa=<id> deep-links a specific CAPA's drawer open (so
  // other surfaces — e.g. Complaints' "View CAPA" — can link to it). Mirrors the S-web-4 ?from=&to=
  // redline pattern. Card/list/raise opens stay local-only (URL untouched) to keep the board unchanged.
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(() => params.get("capa"));
  const [raiseOpen, setRaiseOpen] = useState(false);
  const perms = usePermissions();

  // Open the drawer for ?capa=<id> on mount + whenever the param changes (a deep-link while mounted).
  // Guarded on a non-null id so clearing the param on close never re-opens the drawer.
  useEffect(() => {
    const capa = params.get("capa");
    if (capa) setSelected(capa);
  }, [params]);

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
        <Title order={2} mb="md">
          Nonconformity &amp; CAPA
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the CAPA board. It's available to the Quality Manager, Process
          Owner and Internal Auditor roles.
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
          Nonconformity &amp; CAPA
        </Title>
        <Alert color="red" title="Couldn't load CAPAs">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const openCount = rows.filter((c) => !TERMINAL.includes(c.close_state)).length;
  const bySource = (Object.keys(SOURCE_LABEL) as CapaSource[])
    .map((s) => ({ source: s, n: rows.filter((c) => c.source === s).length }))
    .filter((x) => x.n > 0);

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Nonconformity &amp; CAPA</Title>
        <Group gap="sm">
          {perms.can("capa.create") && (
            <Button onClick={() => setRaiseOpen(true)}>＋ Raise CAPA</Button>
          )}
          <SegmentedControl
            value={view}
            onChange={(v) => setView(v as "board" | "list")}
            data={[
              { value: "board", label: "Board" },
              { value: "list", label: "List" },
            ]}
          />
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2 }} mb="md">
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Open CAPAs
          </Text>
          <Text fz="xl" fw={700}>
            {openCount}
          </Text>
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

      <Group mb="md" gap="sm">
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
          ).map((s) => ({ value: s, label: s }))}
        />
      </Group>

      {filtered.length === 0 ? (
        <Text c="dimmed">No CAPAs match.</Text>
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
          <Table.Tbody>
            {filtered.map((c: Capa) => (
              <Table.Tr
                key={c.id}
                tabIndex={0}
                style={{ cursor: "pointer" }}
                onClick={() => setSelected(c.id)}
                onKeyDown={(e) => {
                  // Keyboard parity with the board's CapaCard buttons: a clickable row must be
                  // focusable + Enter/Space activatable, not mouse-only.
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected(c.id);
                  }
                }}
              >
                <Table.Td>{c.identifier ?? "—"}</Table.Td>
                <Table.Td>{c.title ?? "(untitled)"}</Table.Td>
                <Table.Td>{SEVERITY_LABEL[c.severity]}</Table.Td>
                <Table.Td>{SOURCE_LABEL[c.source]}</Table.Td>
                <Table.Td>{c.close_state}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <CapaDrawer capaId={selected} onClose={closeDrawer} />
      <RaiseCapaModal
        opened={raiseOpen}
        onClose={() => setRaiseOpen(false)}
        onCreated={(id) => setSelected(id)}
      />
    </Container>
  );
}
