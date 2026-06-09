import { Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { ContentBlock } from "../capa/ContentBlock";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "../capa/columns";
import { useCapa, useCapaApproval } from "../capa/hooks";

// The CAPA-subject context on the /tasks decision page: identity + the proposed action plan the approver
// is signing. Both reads are gated capa.read (NOT document.read), so a Top-Management approver works.
export function CapaApprovalContext({ capaId }: { capaId: string }) {
  const { data: capa, isLoading } = useCapa(capaId);
  const { data: approval } = useCapaApproval(capaId);
  if (isLoading || !capa) return <Loader aria-label="Loading CAPA" />;
  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {capa.identifier ?? "CAPA"}
        </Text>
        <Title order={4}>{capa.title ?? "(untitled)"}</Title>
      </div>
      <Group gap="xs">
        <Badge color={SEVERITY_COLOR[capa.severity]} variant="light">
          {SEVERITY_LABEL[capa.severity]}
        </Badge>
        <Badge variant="outline" color="gray">
          {SOURCE_LABEL[capa.source]}
        </Badge>
      </Group>
      <div>
        <Title order={5} mb="xs">
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
