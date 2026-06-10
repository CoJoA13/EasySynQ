import { Alert, Anchor, Card, Group, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { ReviewStateBadge } from "../document/ReviewStateBadge";
import { useDocument } from "../document/useDocument";

// S-web-8: the PERIODIC_REVIEW task's left column — the document under review, loaded BEST-EFFORT
// via document.read. A 403 degrades calmly and never blocks the decision card: the decision
// authority is server-side ownership (live re-check), not this read.
export function PeriodicReviewContext({ documentId }: { documentId: string }) {
  // retry:false — a 403 here is the EXPECTED no-document.read outcome (the calm panel), not a
  // transient; the production QueryClient would otherwise re-hammer the deny 3× with backoff.
  const { data: doc, isLoading, isError, error } = useDocument(documentId, {
    enabled: true,
    retry: false,
  });

  if (isLoading && !doc) return <Text c="dimmed">Loading the document under review…</Text>;
  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color="yellow" title="Document details not visible to you">
        <Text size="sm">
          {status === 403
            ? "You can decide this review, but reading the document isn't granted to you."
            : "Could not load the document under review."}
        </Text>
      </Alert>
    );
  }
  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text ff="monospace" size="sm">{doc.identifier}</Text>
            <Text fw={600}>{doc.title}</Text>
          </div>
          <ReviewStateBadge state={doc.review_state} />
        </Group>
        <Table withRowBorders={false} aria-label="Review context">
          <Table.Tbody>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">State</Text></Table.Td>
              <Table.Td>{doc.current_state}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Effective</Text></Table.Td>
              <Table.Td>{doc.effective_from ? doc.effective_from.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Review period</Text></Table.Td>
              <Table.Td>
                {doc.review_period_months !== null ? `${doc.review_period_months} months` : "—"}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Last reviewed</Text></Table.Td>
              <Table.Td>{doc.last_reviewed_at ? doc.last_reviewed_at.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Next review due</Text></Table.Td>
              <Table.Td>{doc.next_review_due ?? "—"}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to={`/documents/${doc.id}`} size="sm">
          Open the document page →
        </Anchor>
        <Text size="xs" c="dimmed">
          Decided it should be retired instead? Obsolete it from the document page — that is not a
          review outcome.
        </Text>
      </Stack>
    </Card>
  );
}
