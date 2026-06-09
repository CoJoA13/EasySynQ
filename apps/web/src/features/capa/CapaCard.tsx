import { Badge, Card, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import type { Capa } from "../../lib/types";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";

export function CapaCard({ capa, onOpen }: { capa: Capa; onOpen: (id: string) => void }) {
  const muted = capa.close_state === "Rejected" || capa.close_state === "Closed";
  return (
    <UnstyledButton
      onClick={() => onOpen(capa.id)}
      aria-label={`${capa.identifier ?? capa.id} ${capa.title ?? ""}`}
      style={{ display: "block", width: "100%" }}
    >
      <Card withBorder padding="sm" radius="md" opacity={muted ? 0.7 : 1}>
        <Stack gap={6}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed" fw={600}>
              {capa.identifier ?? "—"}
            </Text>
            <Badge size="sm" color={SEVERITY_COLOR[capa.severity]} variant="light">
              {SEVERITY_LABEL[capa.severity]}
            </Badge>
          </Group>
          <Text size="sm" fw={500} lineClamp={2}>
            {capa.title ?? "(untitled)"}
          </Text>
          <Group gap="xs">
            <Badge size="xs" variant="outline" color="gray">
              {SOURCE_LABEL[capa.source]}
            </Badge>
            {capa.close_state === "Rejected" ? (
              <Badge size="xs" variant="light" color="gray">
                Rejected
              </Badge>
            ) : null}
          </Group>
        </Stack>
      </Card>
    </UnstyledButton>
  );
}
