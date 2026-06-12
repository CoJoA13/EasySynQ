import { Container, SimpleGrid, Stack, Title } from "@mantine/core";
import { ActCard } from "./ActCard";
import { CheckCard } from "./CheckCard";
import { DoCard } from "./DoCard";
import { HealthSummary } from "./HealthSummary";
import { MyTasksRail } from "./MyTasksRail";
import { PlanCard } from "./PlanCard";

// The QMS Health home dashboard (doc 11 §5.1): a calm four-quadrant PDCA wheel — counts + RAG only,
// each tile composed from an already-shipped read and degrading independently (spec §5). Ungated (the
// landing page); gating lives on the tiles. N9: status against a rule, never an auto-compliance verdict.
export function HomePage() {
  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <Title order={1}>QMS health</Title>
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
