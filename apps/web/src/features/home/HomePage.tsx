import { Container, Group, SimpleGrid, Stack, Title } from "@mantine/core";
import { AsOf } from "../../lib/AsOf";
import { ActCard } from "./ActCard";
import { CheckCard } from "./CheckCard";
import { DoCard } from "./DoCard";
import { HealthSummary } from "./HealthSummary";
import { MyTasksRail } from "./MyTasksRail";
import { PlanCard } from "./PlanCard";
import { useHomeAsOf } from "./useHomeAsOf";

// The QMS Health home dashboard (doc 11 §5.1): a calm four-quadrant PDCA wheel — counts + RAG only,
// each tile composed from an already-shipped read and degrading independently (spec §5). Ungated (the
// landing page); gating lives on the tiles. N9: status against a rule, never an auto-compliance verdict.
export function HomePage() {
  const asOf = useHomeAsOf();
  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <Group justify="space-between" align="baseline" wrap="nowrap">
          <Title order={1}>QMS health</Title>
          <AsOf at={asOf} />
        </Group>
        <HealthSummary />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <PlanCard />
          <DoCard />
          <CheckCard />
          <ActCard />
        </SimpleGrid>
        <MyTasksRail />
      </Stack>
    </Container>
  );
}
