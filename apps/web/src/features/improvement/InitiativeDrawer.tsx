import { Badge, Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ErrorState, LoadingState } from "../../lib/states";
import type { DirectoryUser } from "../../lib/types";
import { useProcesses } from "../objectives/hooks";
import { InitiativeAdvancePanel } from "./InitiativeAdvancePanel";
import { InitiativeStageBadge } from "./InitiativeStageBadge";
import { InitiativeStageTimeline } from "./InitiativeStageTimeline";
import { SOURCE_LABEL_LONG } from "./labels";
import { useInitiative, useInitiativeStageEvents } from "./hooks";

function nameOf(userId: string, directory: DirectoryUser[]): string {
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}
function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      {typeof children === "string" ? <Text size="sm">{children}</Text> : children}
    </div>
  );
}

export function InitiativeDrawer({
  initiativeId,
  onClose,
}: {
  initiativeId: string | null;
  onClose: () => void;
}) {
  const { data: initiative, isLoading, isError, refetch } = useInitiative(initiativeId);
  const { data: events } = useInitiativeStageEvents(initiativeId);
  const { data: directoryData } = useUserDirectory();
  const { data: processData } = useProcesses();
  const directory = directoryData ?? [];
  const processName = initiative?.process_id
    ? (processData?.find((p) => p.id === initiative.process_id)?.name ?? null)
    : null;

  return (
    <DetailDrawer
      opened={initiativeId !== null}
      onClose={onClose}
      title={
        // Gate the header on !isError too: a failed refetch can leave stale cached data, and we must
        // not show an out-of-date identifier above an error body (the DcrDrawer precedent).
        initiative && !isError ? (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              {initiative.identifier}
            </Text>
            <Title order={4}>{initiative.title}</Title>
          </Stack>
        ) : (
          "Improvement initiative"
        )
      }
    >
      {isLoading ? (
        <LoadingState label="Loading initiative" />
      ) : isError || !initiative ? (
        <ErrorState
          title="Couldn't load this initiative"
          message="It may have been removed, or you may not have access. Close this panel and try again."
          onRetry={() => void refetch()}
        />
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <InitiativeStageBadge stage={initiative.stage} />
            <Badge
              variant="light"
              color="gray"
              aria-label={`Source: ${SOURCE_LABEL_LONG[initiative.source]}`}
            >
              {SOURCE_LABEL_LONG[initiative.source]}
            </Badge>
          </Group>

          <InitiativeAdvancePanel initiative={initiative} />

          {initiative.target_outcome ? (
            <Field label="Target outcome">{initiative.target_outcome}</Field>
          ) : null}
          {initiative.description ? (
            <Field label="Description">{initiative.description}</Field>
          ) : null}

          <Field label="Process">{processName ?? "—"}</Field>
          <Field label="Owner">
            {initiative.owner_user_id ? nameOf(initiative.owner_user_id, directory) : "Unassigned"}
          </Field>

          <Field label="Source">
            {SOURCE_LABEL_LONG[initiative.source]}
            {initiative.source_link_id ? ` · ${initiative.source_link_id.slice(0, 8)}…` : ""}
          </Field>

          <Field label="Opened">{formatDate(initiative.opened_at)}</Field>
          {initiative.closed_at ? (
            <Field label="Closed">{formatDate(initiative.closed_at)}</Field>
          ) : null}

          <Field label="Raised by">
            {`${nameOf(initiative.created_by, directory)} · ${formatDate(initiative.created_at)}`}
          </Field>

          <div>
            <Title order={5} mb="xs">
              History
            </Title>
            <InitiativeStageTimeline events={events ?? []} directory={directory} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
