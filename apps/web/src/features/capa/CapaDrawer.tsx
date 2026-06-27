import { Badge, Button, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { StatusBadge } from "../../lib/StatusBadge";
import { ErrorState, LoadingState } from "../../lib/states";
import { SpawnDcrModal } from "../dcr/SpawnDcrModal";
import { useRaiseDcrFromCapa } from "../dcr/mutations";
import { AdvancePanel } from "./AdvancePanel";
import { CLOSE_STATE_LABEL, SOURCE_LABEL } from "./columns";
import { CapaTimeline } from "./CapaTimeline";
import { CloseGateStepper } from "./CloseGateStepper";
import { SeverityBadge } from "./SeverityBadge";
import { useCapa } from "./hooks";
import { useCapaSetTargetDate } from "./mutations";

export function CapaDrawer({ capaId, onClose }: { capaId: string | null; onClose: () => void }) {
  const { data: capa, isLoading, isError, refetch } = useCapa(capaId);
  const { data: directory } = useUserDirectory();
  const scope: { level: string; id?: string } = capa?.process_id
    ? { level: "PROCESS", id: capa.process_id }
    : { level: "SYSTEM" };
  const { can } = usePermissions(scope);
  const raiseDcr = useRaiseDcrFromCapa(capaId ?? "");
  const setTargetDate = useCapaSetTargetDate(capaId ?? "");
  const [raisingDcr, setRaisingDcr] = useState(false);
  const [targetDate, setTargetDateInput] = useState("");

  useEffect(() => {
    setTargetDateInput(capa?.target_completion_date ?? "");
  }, [capaId, capa?.target_completion_date]);

  return (
    <DetailDrawer
      opened={capaId !== null}
      onClose={onClose}
      title={
        // Gate the header on !isError too: a failed refetch can leave stale cached data, and we must not
        // show an out-of-date identifier/title above an error body.
        capa && !isError ? (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              {capa.identifier ?? "CAPA"}
            </Text>
            <Title order={4}>{capa.title ?? "(untitled)"}</Title>
          </Stack>
        ) : (
          "CAPA"
        )
      }
    >
      {isLoading ? (
        <LoadingState label="Loading CAPA" />
      ) : isError || !capa ? (
        <ErrorState
          title="Couldn't load this CAPA"
          message="It may have been removed, or you may not have access. Close this panel and try again."
          onRetry={() => refetch()}
        />
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <SeverityBadge severity={capa.severity} />
            <Badge variant="outline" color="gray">
              {SOURCE_LABEL[capa.source]}
            </Badge>
            <Badge variant="light" color="blue">
              {CLOSE_STATE_LABEL[capa.close_state]}
            </Badge>
            {capa.cycle_marker > 0 ? (
              <Badge variant="light" color="grape">
                Loop ×{capa.cycle_marker}
              </Badge>
            ) : null}
          </Group>

          <div>
            <Title order={5} mb="sm">
              Closed-loop thread
            </Title>
            <CapaTimeline
              stages={capa.stages ?? []}
              directory={directory ?? []}
              capaId={capa.id}
              cycleMarker={capa.cycle_marker}
              closeState={capa.close_state}
            />
          </div>

          <div>
            <Title order={5} mb="sm">
              Close gate
            </Title>
            <CloseGateStepper stages={capa.stages ?? []} cycleMarker={capa.cycle_marker} />
          </div>

          {/* S-capa-overdue: target completion date + overdue badge + inline edit (capa.update) */}
          <div>
            <Title order={5} mb="sm">
              Target completion
            </Title>
            <Group gap="xs" mb={can("capa.update") ? "xs" : undefined}>
              <Text size="sm">{capa.target_completion_date ?? "—"}</Text>
              {capa.overdue && <StatusBadge tone="danger" label="Overdue" kind="CAPA" />}
            </Group>
            {can("capa.update") && (
              <Group gap="xs" align="flex-end">
                <TextInput
                  type="date"
                  label="Set target date"
                  value={targetDate}
                  onChange={(e) => setTargetDateInput(e.currentTarget.value)}
                  size="xs"
                />
                <Button
                  size="xs"
                  loading={setTargetDate.isPending}
                  onClick={() => setTargetDate.mutate(targetDate || null)}
                >
                  Save
                </Button>
              </Group>
            )}
          </div>

          <div>
            <Title order={5} mb="sm">
              Next step
            </Title>
            <AdvancePanel capa={capa} />
          </div>

          {/* Hide on a terminal CAPA: the backend raise_dcr_from_capa deterministically 409s `capa_terminal`
              for a Closed/Rejected CAPA, so showing the button would be a show-then-409 (Codex #6). */}
          {can("changeRequest.create") && !["Closed", "Rejected"].includes(capa.close_state) && (
            <Button
              size="xs"
              variant="light"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setRaisingDcr(true)}
            >
              Raise change request
            </Button>
          )}

          {raisingDcr && (
            <SpawnDcrModal
              title="Raise a change request from this CAPA"
              mutation={raiseDcr}
              onClose={() => setRaisingDcr(false)}
            />
          )}
        </Stack>
      )}
    </DetailDrawer>
  );
}
