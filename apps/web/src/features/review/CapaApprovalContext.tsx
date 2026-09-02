import { Badge, Group, Stack, Text, Title } from "@mantine/core";
import { ApiError } from "../../lib/api";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { ContentBlock } from "../capa/ContentBlock";
import { SOURCE_LABEL } from "../capa/columns";
import { SeverityBadge } from "../capa/SeverityBadge";
import { useCapa, useCapaApproval } from "../capa/hooks";

// The CAPA-subject context on the /tasks decision page: identity + the proposed action plan the approver
// is signing. Both reads are gated capa.read (NOT document.read), so a Top-Management approver works.
export function CapaApprovalContext({ capaId }: { capaId: string }) {
  const { data: capa, isLoading, isError, error, refetch } = useCapa(capaId);
  const { data: approval } = useCapaApproval(capaId);
  if (isLoading) return <LoadingState label="Loading CAPA" />;
  if (isError || !capa) {
    if (error instanceof ApiError && error.status === 403) {
      return <NoAccessState message="You don't have access to this CAPA." />;
    }
    return <ErrorState title="Couldn't load this CAPA" onRetry={() => void refetch()} />;
  }
  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {capa.identifier ?? "CAPA"}
        </Text>
        <Title order={2} size="h3">{capa.title ?? "(untitled)"}</Title>
      </div>
      <Group gap="xs">
        <SeverityBadge severity={capa.severity} />
        <Badge variant="outline" color="gray">
          {SOURCE_LABEL[capa.source]}
        </Badge>
      </Group>
      <div>
        <Title order={3} size="h4" mb="xs">
          Proposed action plan
        </Title>
        {approval?.proposed_action_plan ? (
          <ContentBlock block={approval.proposed_action_plan} />
        ) : (
          <Text size="sm" c="dimmed">
            No action plan is attached to this approval.
          </Text>
        )}
      </div>
    </Stack>
  );
}
