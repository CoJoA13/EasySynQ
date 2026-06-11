import { Alert, Badge, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useParams } from "react-router-dom";
import { useObjective } from "./hooks";
import { CommitmentHero } from "./CommitmentHero";
import { PlansSection } from "./PlansSection";
import { MeasurementsSection } from "./MeasurementsSection";

export function ObjectiveDetailPage() {
  const { id = null } = useParams();
  const { data: o, isLoading, isError, forbidden } = useObjective(id);

  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  if (isError || !o) {
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this objective">
          {forbidden
            ? "You don't have access to this objective."
            : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4} aria-label="Objective reference">
            <Text c="dimmed" size="sm" fw={500}>{o.identifier}</Text>
            <Badge color="gray" variant="light">{o.current_state}</Badge>
          </Group>
          <Title order={2}>{o.title}</Title>
        </div>
        <CommitmentHero objective={o} />
        <PlansSection objectiveId={o.id} plans={o.plans} />
        <MeasurementsSection objectiveId={o.id} unit={o.unit} />
      </Stack>
    </Container>
  );
}
