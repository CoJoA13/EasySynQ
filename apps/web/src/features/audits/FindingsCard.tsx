import { Alert, Button, Group, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Audit, CapaCloseState, Finding } from "../../lib/types";
import { useCapas } from "../capa/hooks";
import { FindingPanel } from "./FindingPanel";
import { useFindings } from "./hooks";

// blocking mirrors the backend finding_blocks_close: a LIVE (non-superseded) NC whose auto-CAPA
// is not Closed. Advisory only — the server 409 is the authority (degrade without capa.read).
function isBlocking(f: Finding, capaStates: Map<string, CapaCloseState>): boolean {
  if (f.finding_type !== "NC" || f.superseded_by_correction !== null) return false;
  if (!f.auto_capa_id) return true;
  return capaStates.get(f.auto_capa_id) !== "Closed";
}

export function FindingsCard({
  audit,
  scope,
  onLog,
  onCorrect,
}: {
  audit: Audit;
  scope: { level: string; id?: string };
  onLog: () => void;
  onCorrect: (finding: Finding) => void;
}) {
  const findings = useFindings(audit.id);
  const perms = usePermissions(scope);
  const capas = useCapas(); // cross-ref for the per-NC CAPA state chips; degrades on 403
  const closed = audit.state === "Closed";
  const canCreate = !perms.isLoading && perms.can("finding.create");

  if (findings.forbidden) {
    return (
      <Paper withBorder p="md">
        <Title order={5} mb="xs">
          Findings
        </Title>
        <Text size="sm" c="dimmed">
          You don't have access to findings (<code>finding.read</code>).
        </Text>
      </Paper>
    );
  }
  if (findings.isLoading) {
    return (
      <Paper withBorder p="md">
        <Loader size="sm" />
      </Paper>
    );
  }

  const rows = findings.data ?? [];
  const capaStates = new Map<string, CapaCloseState>(
    (capas.forbidden ? [] : (capas.data ?? [])).map((c) => [c.id, c.close_state]),
  );
  const blocking =
    capas.forbidden ? 0 : rows.filter((f) => isBlocking(f, capaStates)).length;
  // isSuccess: while the CAPA list is loading, the empty map would over-count every live NC as
  // blocking — wait for the resolved list so the note is honest in both directions.
  const showReadiness =
    capas.isSuccess && (audit.state === "Reported" || audit.state === "Closing") && blocking > 0;

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="sm">
        <Title order={5}>Findings ({rows.length})</Title>
        {canCreate && !closed && (
          <Button size="xs" variant="light" onClick={onLog}>
            ＋ Log finding
          </Button>
        )}
      </Group>
      {closed && (
        <Text size="sm" c="dimmed" mb="sm">
          Findings are closed with the audit.
        </Text>
      )}
      {showReadiness && (
        <Alert color="orange" mb="sm" title="Close readiness">
          {blocking} live NC finding{blocking === 1 ? "" : "s"} without a Closed CAPA — closing
          will be blocked. Close the CAPA, or correct the finding to Observation/OFI.
        </Alert>
      )}
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed">
          No findings logged yet.
        </Text>
      ) : (
        <Stack gap="sm">
          {rows.map((f) => (
            <FindingPanel
              key={f.id}
              finding={f}
              capaState={
                f.auto_capa_id && !capas.forbidden ? capaStates.get(f.auto_capa_id) : undefined
              }
              canCorrect={canCreate && !closed}
              onCorrect={onCorrect}
            />
          ))}
        </Stack>
      )}
    </Paper>
  );
}
