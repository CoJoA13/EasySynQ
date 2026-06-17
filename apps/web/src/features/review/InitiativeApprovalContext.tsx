import { Alert, Badge, Loader, Stack, Text, Title } from "@mantine/core";
import { INITIATIVE_STAGE_META } from "../improvement/labels";
import { useInitiative } from "../improvement/hooks";

// S-improvement-4: the initiative-subject context on the /tasks decision page — the identity + target
// outcome the Top-Management member is verifying before they sign. Read BEST-EFFORT via
// improvement.read (a separate gate from candidate-pool membership); a 403 degrades calmly and never
// blocks the decision card (authority is pool membership, server-side — the DcrApprovalContext shape).
export function InitiativeApprovalContext({ initiativeId }: { initiativeId: string }) {
  const { data: initiative, isLoading, isError, forbidden } = useInitiative(initiativeId);

  if (isLoading) return <Loader aria-label="Loading the initiative" />;
  if (isError || !initiative) {
    return (
      <Alert color="yellow" title="Initiative not visible to you">
        <Text size="sm">
          {forbidden
            ? "You can authorize this initiative, but reading it isn't granted to you."
            : "Could not load the improvement initiative."}
        </Text>
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {initiative.identifier}
        </Text>
        <Title order={3}>{initiative.title}</Title>
      </div>
      <Badge variant="light" color="gray">
        {INITIATIVE_STAGE_META[initiative.stage].label}
      </Badge>
      {initiative.description ? (
        <div>
          <Text size="xs" c="dimmed">
            Description
          </Text>
          <Text size="sm">{initiative.description}</Text>
        </div>
      ) : null}
      {initiative.target_outcome ? (
        <div>
          <Text size="xs" c="dimmed">
            Intended improvement
          </Text>
          <Text size="sm">{initiative.target_outcome}</Text>
        </div>
      ) : null}
    </Stack>
  );
}
