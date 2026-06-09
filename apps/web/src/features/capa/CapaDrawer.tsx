import { Alert, Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { CapaTimeline } from "./CapaTimeline";
import { CloseGateStepper } from "./CloseGateStepper";
import { useCapa } from "./hooks";

export function CapaDrawer({ capaId, onClose }: { capaId: string | null; onClose: () => void }) {
  const { data: capa, isLoading, isError } = useCapa(capaId);
  const { data: directory } = useUserDirectory();

  return (
    <DetailDrawer
      opened={capaId !== null}
      onClose={onClose}
      title={
        capa && !isError ? (
          // Gate the title on !isError too, so a failed refetch with stale cached data doesn't leave
          // an out-of-date identifier/title in the header while the body shows the error.
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
        <Loader />
      ) : isError || !capa ? (
        // The detail fetch failed (a stale board row, a 404/403/500, or a permission change between
        // the list and detail reads) — surface it calmly instead of spinning forever. `isError` is
        // honored even when React Query still holds stale cached data from an earlier successful open,
        // so a later failed refetch never renders out-of-date details.
        <Alert color="red" title="Couldn't load this CAPA">
          It may have been removed, or you may not have access. Close this panel and try again.
        </Alert>
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <Badge color={SEVERITY_COLOR[capa.severity]} variant="light">
              {SEVERITY_LABEL[capa.severity]}
            </Badge>
            <Badge variant="outline" color="gray">
              {SOURCE_LABEL[capa.source]}
            </Badge>
            <Badge variant="light" color="blue">
              {capa.close_state}
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
            <CapaTimeline stages={capa.stages ?? []} directory={directory ?? []} />
          </div>

          <div>
            <Title order={5} mb="sm">
              Close gate
            </Title>
            <CloseGateStepper
              stages={capa.stages ?? []}
              closeState={capa.close_state}
              cycleMarker={capa.cycle_marker}
            />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
