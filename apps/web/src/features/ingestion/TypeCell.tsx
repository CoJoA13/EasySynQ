import { Stack, Text } from "@mantine/core";
import type { ImportClassification } from "../../lib/types";

// The type cell — renders the EFFECTIVE type (no label lookup; the code is the label here). A
// "Correct to type" decision folds onto review.type_code, so the caller passes that as
// `effectiveTypeCode`; it wins over the immutable classifier proposal. When neither is set the cell
// degrades to a plain dash. `classification.ambiguous` adds a small `ambiguous` caption (the mockup's
// alt-type hint) — only meaningful while the classifier proposal is what's being shown.
export function TypeCell({
  effectiveTypeCode = null,
  classification,
}: {
  effectiveTypeCode?: string | null;
  classification: ImportClassification | null;
}) {
  const code = effectiveTypeCode ?? classification?.type_code ?? null;
  if (!code) {
    return (
      <Text span size="sm" c="dimmed">
        —
      </Text>
    );
  }
  // Show the ambiguous hint only when we're displaying the classifier's own proposal (no human
  // correction has overridden it) — a corrected value is human-confirmed, never "ambiguous".
  const showAmbiguous = effectiveTypeCode == null && classification?.ambiguous === true;
  return (
    <Stack gap={2} align="flex-start">
      <Text span size="sm">
        {code}
      </Text>
      {showAmbiguous && (
        <Text span size="xs" c="dimmed">
          ambiguous
        </Text>
      )}
    </Stack>
  );
}
