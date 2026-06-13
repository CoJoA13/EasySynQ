import { Alert, Anchor, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReview } from "../management-review/hooks";

// S-mr-2: the MGMT_REVIEW task's left column — the review under preparation/action, loaded
// BEST-EFFORT via mgmtReview.read (retry:false → a 403 is the expected no-read outcome, not a
// transient). A forbidden/error read degrades calmly and NEVER blocks the card: the action's
// completion authority is server-side, not this read.
export function MgmtReviewContext({ reviewId }: { reviewId: string }) {
  const { data: mr, isLoading, isError, forbidden } = useMgmtReview(reviewId);

  if (isLoading && !mr) return <Text c="dimmed">Loading the management review…</Text>;
  if (isError || !mr) {
    return (
      <Alert color="yellow" title="Review details not visible to you">
        <Text size="sm">
          {forbidden
            ? "You can act on this task, but reading the management review isn't granted to you."
            : "Could not load the management review."}
        </Text>
      </Alert>
    );
  }
  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text ff="monospace" size="sm">
              {mr.identifier}
            </Text>
            <Text fw={600}>{mr.title}</Text>
          </div>
          <StateBadge state={mr.current_state} />
        </Group>
        {(mr.period_label || mr.review_date) && (
          <Text size="sm" c="dimmed">
            {mr.period_label ?? ""}
            {mr.review_date ? `${mr.period_label ? " · " : ""}${mr.review_date}` : ""}
          </Text>
        )}
        <Anchor component={Link} to={`/management-reviews/${mr.id}`} size="sm">
          Open the review page →
        </Anchor>
      </Stack>
    </Card>
  );
}
