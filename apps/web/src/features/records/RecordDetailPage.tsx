import { Alert, Anchor, Badge, Container, Group, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { humanizeToken } from "../../lib/labels";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { useFreshRecordData, useRecord } from "./hooks";
import { RecordDetailSections } from "./RecordDetailSections";
import { RecordDownloadButton } from "./RecordDownloadButton";

function recordsOrigin(state: unknown): string {
  if (state !== null && typeof state === "object" && "from" in state) {
    const from = (state as { from?: unknown }).from;
    if (typeof from === "string" && from.startsWith("/records")) return from;
  }
  return "/records";
}

function BackToRecords({ to }: { to: string }) {
  return (
    <Anchor component={Link} to={to} aria-label="Back to records">
      ← Back to records
    </Anchor>
  );
}

export function RecordDetailPage() {
  const { recordId = null } = useParams();
  return <RecordDetailRoute key={recordId ?? "missing-record"} recordId={recordId} />;
}

function RecordDetailRoute({ recordId }: { recordId: string | null }) {
  const location = useLocation();
  const recordQuery = useRecord(recordId);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const record = useFreshRecordData(recordId, recordQuery);
  const backTo = recordsOrigin(location.state);

  useEffect(() => {
    if (record) headingRef.current?.focus();
  }, [record]);

  if (!recordQuery.isError && !record) {
    return (
      <Container size="xl" py="md">
        <LoadingState label="Loading record" />
      </Container>
    );
  }

  if (recordQuery.isError || !record) {
    const status = recordQuery.error instanceof ApiError ? recordQuery.error.status : 0;
    return (
      <Container size="xl" py="md">
        <Stack gap="md" align="flex-start">
          {status === 403 ? (
            <NoAccessState message="Record access is unavailable." />
          ) : status === 404 ? (
            <Alert color="gray" title="Record not found">
              This record could not be found.
            </Alert>
          ) : (
            <ErrorState
              title="Couldn't load this record"
              onRetry={() => void recordQuery.refetch()}
            />
          )}
          <BackToRecords to={backTo} />
        </Stack>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Stack gap="lg">
        <Stack gap="xs">
          <BackToRecords to={backTo} />
          <Text c="dimmed" fw={600} size="sm">
            {record.identifier ?? "Record"}
          </Text>
          <Title order={2} ref={headingRef} tabIndex={-1}>
            {record.title}
          </Title>
          <Group gap="xs" aria-label="Record state">
            <Badge variant="light">{humanizeToken(record.record_type)}</Badge>
            <Badge variant="outline">{record.classification}</Badge>
            <Badge variant="outline">{humanizeToken(record.disposition_state)}</Badge>
            <Badge variant="outline">{record.legal_hold ? "Legal hold" : "No legal hold"}</Badge>
          </Group>
          {record.has_structured_pdf && (
            <RecordDownloadButton
              label="Download structured PDF"
              endpoint={`/api/v1/records/${record.id}/rendition`}
              pendingIsNormal
            />
          )}
        </Stack>
        <RecordDetailSections record={record} />
      </Stack>
    </Container>
  );
}
