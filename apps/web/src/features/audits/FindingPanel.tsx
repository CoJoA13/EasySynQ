import { Anchor, Badge, Button, Group, Paper, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { CapaCloseState, Finding } from "../../lib/types";
import { FindingTypeBadge } from "./badges";

// One finding. capaState: the cross-ref'd CAPA close_state (undefined = capa.read denied →
// chip omitted; the deep-link always renders — the board page enforces its own access).
const CAPA_STATE_LABEL: Record<CapaCloseState, string> = {
  Raised: "Raised", Containment: "Containment", RootCause: "Root cause",
  ActionPlan: "Action plan", Implement: "Implement", Verify: "Verify",
  Closed: "Closed", Rejected: "Rejected",
};

export function FindingPanel({
  finding,
  capaState,
  canCorrect,
  onCorrect,
}: {
  finding: Finding;
  capaState: CapaCloseState | undefined;
  canCorrect: boolean;
  onCorrect: (finding: Finding) => void;
}) {
  const superseded = finding.superseded_by_correction !== null;
  return (
    <Paper withBorder p="sm" data-finding style={{ opacity: superseded ? 0.6 : 1 }}>
      <Group justify="space-between" mb={4}>
        <Text size="sm" fw={600}>
          {finding.identifier ?? finding.id.slice(0, 8)}
        </Text>
        <FindingTypeBadge type={finding.finding_type} severity={finding.severity} />
      </Group>
      <Text size="sm" mb={4}>
        {finding.title ?? "—"}
      </Text>
      <Group gap="xs" mb={4}>
        {finding.clause_ref && <Badge variant="outline" color="gray">{finding.clause_ref}</Badge>}
        {finding.process_ref && (
          <Badge variant="outline" color="gray">
            {finding.process_ref}
          </Badge>
        )}
      </Group>
      {superseded && (
        <Text size="xs" c="dimmed">
          ✕ Superseded by correction
        </Text>
      )}
      {finding.correction_of && (
        <Text size="xs" c="dimmed">
          ↪ Corrects an earlier finding
        </Text>
      )}
      <Group justify="space-between" mt={4}>
        <Group gap="xs">
          {finding.auto_capa_id && capaState !== undefined && (
            <Badge variant="light" color={capaState === "Closed" ? "green" : "orange"}>
              CAPA: {CAPA_STATE_LABEL[capaState]}
            </Badge>
          )}
          {finding.auto_capa_id && (
            <Anchor component={Link} size="sm" to={`/capa?capa=${finding.auto_capa_id}`}>
              View CAPA →
            </Anchor>
          )}
        </Group>
        {canCorrect && !superseded && (
          <Button size="xs" variant="subtle" onClick={() => onCorrect(finding)}>
            Correct
          </Button>
        )}
      </Group>
    </Paper>
  );
}
