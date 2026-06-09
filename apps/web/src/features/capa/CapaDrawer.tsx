import { Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { CapaTimeline } from "./CapaTimeline";
import { CloseGateStepper } from "./CloseGateStepper";
import { useCapa } from "./hooks";

export function CapaDrawer({ capaId, onClose }: { capaId: string | null; onClose: () => void }) {
  const { data: capa, isLoading } = useCapa(capaId);
  const { data: directory } = useUserDirectory();

  return (
    <DetailDrawer
      opened={capaId !== null}
      onClose={onClose}
      title={
        capa ? (
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
      {isLoading || !capa ? (
        <Loader />
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
            <CloseGateStepper stages={capa.stages ?? []} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
