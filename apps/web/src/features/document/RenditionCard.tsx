import { Badge, Button, Card, Group, Stack, Text } from "@mantine/core";
import type { DocumentSummary } from "../../lib/types";
import { StateBadge } from "./StateBadge";
import { useControlledCopyDownload } from "./download";

// S-web-4 (D-C): the controlled-copy rendition card — rendition-state + an Open/Download action.
// No embedded PDF.js this slice; the watermarked PDF opens in a new tab via the presigned GET
// (access is logged server-side). An honest empty state when no governing version exists.
export function RenditionCard({ doc }: { doc: DocumentSummary }) {
  const { open, downloading, rendition } = useControlledCopyDownload(doc.id);
  const hasRendition = doc.current_effective_version_id !== null;

  return (
    <Card withBorder aria-label="Controlled rendition">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text fw={600}>Controlled rendition</Text>
            <Text size="sm" c="dimmed">
              Read-only view of the governing copy — watermarked on every copy.
            </Text>
          </div>
          {hasRendition && <StateBadge state={doc.current_state} />}
        </Group>

        {hasRendition ? (
          <>
            <Group gap="sm" align="center">
              <Button variant="light" loading={downloading} onClick={() => void open()}>
                ⤢ Open controlled copy
              </Button>
              <Text size="xs" c="dimmed">
                Opens the watermarked PDF in a new tab · access logged
              </Text>
            </Group>
            {rendition === "source" && (
              <Badge variant="light" color="var(--es-warning)">
                Controlled PDF still rendering — opened the source file
              </Badge>
            )}
          </>
        ) : (
          <Text size="sm" c="dimmed">
            No governing rendition yet — this document has no effective version.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
