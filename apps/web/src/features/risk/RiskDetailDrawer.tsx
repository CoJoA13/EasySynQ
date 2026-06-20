import { Alert, Anchor, Box, Button, Divider, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { usePermissions } from "../../app/shell/usePermissions";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { StatusBadge } from "../../lib/StatusBadge";
import { RISK_BAND_LABEL, RISK_TYPE_LABEL } from "./labels";
import { useRisk } from "./hooks";
import { useSpawnRiskCapa } from "./mutations";
import { EditRiskModal } from "./EditRiskModal";

// The risk detail drawer (the CapaDrawer/InitiativeDrawer idiom): prop-driven riskId, opened on
// riskId !== null; the page owns the ?risk= URL wiring. Shows the score/treatment, the gated Edit
// (register.manage @ the row's process AND headEditable), and the risk→CAPA spawn seam. There is no
// server `capabilities` block on a risk row (unlike Objective/CAPA), so the drawer gates on a
// usePermissions probe at the row's OWN process (SYSTEM for an org-level row) + the head state.
export function RiskDetailDrawer({
  riskId,
  onClose,
  headEditable,
}: {
  riskId: string | null;
  onClose: () => void;
  headEditable: boolean;
}) {
  const { data: risk, isLoading, isError, forbidden, refetch } = useRisk(riskId);
  // Probe at the row's OWN process (exact — the drawer has the row); SYSTEM for an org-level (null)
  // row. The PROCESS probe returns the EFFECTIVE decision (matching SYSTEM grants AND any scoped
  // DENY), so for a process-owned row use it ALONE — OR-ing the raw SYSTEM result would bypass a
  // process-level deny that carves this process out of a broader SYSTEM grant (deny-wins; Codex P3).
  // Keyed on the permission KEY, never on process-count.
  const sys = usePermissions();
  const proc = usePermissions(
    risk?.process_id ? { level: "PROCESS", id: risk.process_id } : undefined,
  );
  const hasProc = !!risk?.process_id;
  const canManage = hasProc ? proc.can("register.manage") : sys.can("register.manage");
  const canSpawn = hasProc ? proc.can("capa.create") : sys.can("capa.create");

  const spawn = useSpawnRiskCapa(riskId ?? "");
  const [editOpen, setEditOpen] = useState(false);
  const [spawnError, setSpawnError] = useState<string | null>(null);

  async function doSpawn() {
    setSpawnError(null);
    try {
      await spawn.mutateAsync();
    } catch (e) {
      setSpawnError(e instanceof ApiError ? e.message : "Could not spawn a CAPA.");
    }
  }

  return (
    <DetailDrawer
      opened={riskId !== null}
      onClose={onClose}
      title={risk && !isError ? `${RISK_TYPE_LABEL[risk.type]} detail` : "Risk detail"}
    >
      {isLoading ? (
        <LoadingState label="Loading risk" />
      ) : isError || !risk ? (
        forbidden ? (
          <NoAccessState message="You don't have access to this risk." />
        ) : (
          <ErrorState
            title="Couldn't load this risk"
            message="Something went wrong. Please try again."
            onRetry={() => refetch()}
          />
        )
      ) : (
        <Stack gap="md">
          <Group gap="xs" wrap="wrap">
            <StatusBadge
              tone={risk.band_tone}
              label={RISK_BAND_LABEL[risk.band]}
              kind={RISK_TYPE_LABEL[risk.type]}
            />
            {risk.type === "opportunity" && (
              <Text size="sm" c="dimmed">
                Opportunity
              </Text>
            )}
          </Group>
          <Text>{risk.description}</Text>

          <Divider />
          <Box>
            <Text size="sm" fw={600}>
              Score
            </Text>
            <Text size="sm" c="dimmed">
              Likelihood {risk.likelihood} × Severity {risk.severity} = rating {risk.risk_rating} (
              {RISK_BAND_LABEL[risk.band]})
            </Text>
          </Box>

          <Box>
            <Text size="sm" fw={600}>
              Treatment
            </Text>
            <Text size="sm" c={risk.treatment ? undefined : "dimmed"}>
              {risk.treatment || "No treatment recorded yet."}
            </Text>
            {risk.effectiveness && (
              <Text size="sm" mt={4}>
                <Text span fw={600} size="sm">
                  Effectiveness:{" "}
                </Text>
                {risk.effectiveness}
              </Text>
            )}
          </Box>

          {/* The risk → CAPA spawn seam. The linked-CAPA reference shows for ANY row that has one —
              so a risk reclassified to an opportunity keeps its traceability link (Codex P2); only the
              SPAWN affordance is risk-only (the server 422s an opportunity spawn). The spawn is gated
              capa.create @ the row's process, NOT on headEditable (operational, any head state). */}
          {(risk.type === "risk" || risk.linked_capa_id) && (
            <>
              <Divider />
              <Box>
                <Text size="sm" fw={600} mb={4}>
                  Corrective action
                </Text>
                {risk.linked_capa_id ? (
                  <Group gap="xs">
                    <StatusBadge tone="info" label="CAPA raised" kind="Treatment" />
                    <Anchor component={Link} to={`/capa?capa=${risk.linked_capa_id}`} size="sm">
                      Open the linked CAPA
                    </Anchor>
                  </Group>
                ) : risk.type === "risk" && canSpawn ? (
                  <Stack gap="xs">
                    {spawnError && <Alert color="red">{spawnError}</Alert>}
                    <Button
                      variant="light"
                      onClick={() => void doSpawn()}
                      loading={spawn.isPending}
                    >
                      Treat → spawn CAPA
                    </Button>
                    <Text size="xs" c="dimmed">
                      Raises a corrective action to treat this risk (severity set from the band).
                    </Text>
                  </Stack>
                ) : (
                  <Text size="sm" c="dimmed">
                    No corrective action raised.
                  </Text>
                )}
              </Box>
            </>
          )}

          {canManage && headEditable && (
            <>
              <Divider />
              <Group justify="flex-end">
                <Button variant="default" onClick={() => setEditOpen(true)}>
                  Edit risk
                </Button>
              </Group>
            </>
          )}
          {canManage && !headEditable && (
            <Text size="xs" c="dimmed">
              The register isn't in an editable state — risks are read-only. See the register banner
              for the current step.
            </Text>
          )}

          {editOpen && <EditRiskModal opened onClose={() => setEditOpen(false)} risk={risk} />}
        </Stack>
      )}
    </DetailDrawer>
  );
}
