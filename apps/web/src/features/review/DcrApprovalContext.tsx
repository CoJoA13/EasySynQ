import { Alert, Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { CHANGE_TYPE_LABEL, REASON_LABEL } from "../dcr/labels";
import { DcrImpactTable } from "../dcr/DcrImpactTable";
import { DcrStateBadge } from "../dcr/DcrStateBadge";
import { useDcr, useDcrImpact } from "../dcr/hooks";

// The DCR-subject context on the /tasks decision page: identity + reason + impact the approver is
// signing. Read BEST-EFFORT via changeRequest.read (a separate gate from candidate-pool membership);
// a 403 degrades calmly and never blocks the decision card (authority is pool membership, server-side).
export function DcrApprovalContext({ dcrId }: { dcrId: string }) {
  const { data: dcr, isLoading, isError, forbidden } = useDcr(dcrId);
  const { data: impact } = useDcrImpact(dcrId);

  if (isLoading) return <Loader aria-label="Loading the change request" />;
  if (isError || !dcr) {
    return (
      <Alert color="yellow" title="Change request not visible to you">
        <Text size="sm">
          {forbidden
            ? "You can decide this approval, but reading the change request isn't granted to you."
            : "Could not load the change request."}
        </Text>
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {dcr.identifier}
        </Text>
        <Title order={3}>{CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}</Title>
      </div>
      <Group gap="xs">
        <DcrStateBadge state={dcr.state} />
        <Badge variant="light" color="gray">
          {dcr.change_significance}
        </Badge>
        <Badge variant="light" color="gray">
          {REASON_LABEL[dcr.reason_class] ?? dcr.reason_class}
        </Badge>
      </Group>
      <div>
        <Text size="xs" c="dimmed">
          Reason
        </Text>
        <Text size="sm">{dcr.reason_text}</Text>
      </div>
      <div>
        <Title order={4} mb="xs">
          Impact assessment
        </Title>
        <DcrImpactTable impact={impact ?? []} />
      </div>
    </Stack>
  );
}
