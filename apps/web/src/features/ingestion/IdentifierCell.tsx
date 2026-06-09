import { Text } from "@mantine/core";
import type { ImportFileReview } from "../../lib/types";

// The proposed identifier cell. Order: a within-import duplicate (danger) wins; else the folded
// effective identifier (mono); else a tertiary hint that depends on the kind — a RECORD legitimately
// has no doc code, a document still needs one suggested. Null review → a plain dash.
export function IdentifierCell({
  review,
  dupeOf,
}: {
  review: ImportFileReview | null;
  dupeOf: string | null;
}) {
  if (dupeOf) {
    return (
      <Text span size="sm" c="var(--es-danger)">
        Duplicate of {dupeOf}
      </Text>
    );
  }
  if (review?.identifier) {
    return (
      <Text span size="sm" ff="monospace">
        {review.identifier}
      </Text>
    );
  }
  if (review?.kind === "RECORD") {
    return (
      <Text span size="sm" c="dimmed">
        — record (no code)
      </Text>
    );
  }
  if (review) {
    return (
      <Text span size="sm" c="dimmed">
        — suggest needed
      </Text>
    );
  }
  return (
    <Text span size="sm" c="dimmed">
      —
    </Text>
  );
}
