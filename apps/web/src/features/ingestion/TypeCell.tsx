import { Stack, Text } from "@mantine/core";
import type { ImportClassification } from "../../lib/types";

// The proposed type cell — renders the engine's type_code verbatim (no label lookup; the code is the
// label here). `classification.ambiguous` adds a small `ambiguous` caption (the mockup's alt-type
// hint). A null classification or a missing type_code degrades to a plain dash.
export function TypeCell({ classification }: { classification: ImportClassification | null }) {
  if (!classification || !classification.type_code) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  return (
    <Stack gap={2} align="flex-start">
      <Text span size="sm">
        {classification.type_code}
      </Text>
      {classification.ambiguous && (
        <Text span size="xs" c="dimmed">
          ambiguous
        </Text>
      )}
    </Stack>
  );
}
