import { Badge, Group, Stack, Text } from "@mantine/core";
import { ApiError } from "../../lib/api";
import { EmptyState, InlineState, LoadingState } from "../../lib/states";
import { useDocumentVersions } from "./useDocumentVersions";

// The History tab: the immutable version timeline (newest first), read-only. Gated
// document.read_draft server-side — a 403 renders as quiet "no access" (DP-6), not an error.
export function HistoryTab({ documentId, active }: { documentId: string | null; active: boolean }) {
  const { data, isLoading, isError, error } = useDocumentVersions(documentId, active);

  if (isLoading) return <LoadingState label="Loading version history" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403) {
      return (
        <InlineState kind="forbidden">You don't have access to the version history.</InlineState>
      );
    }
    return <InlineState kind="error">Could not load version history.</InlineState>;
  }
  if (!data || data.length === 0) {
    return <EmptyState message="No versions yet." />;
  }

  return (
    <Stack gap="md" aria-label="Version history">
      {data.map((v) => (
        <Stack key={v.id} gap={2}>
          <Group gap="sm">
            <Text fw={600} size="sm">
              {v.revision_label}
            </Text>
            <Badge variant="light" size="sm">
              {v.version_state}
            </Badge>
            {v.effective_from && (
              <Text size="xs" c="dimmed">
                {v.effective_from.slice(0, 10)}
              </Text>
            )}
          </Group>
          {v.change_reason && (
            <Text size="sm" c="dimmed">
              {v.change_reason}
            </Text>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
