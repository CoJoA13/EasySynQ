import { Alert, Button, Container, Group, Title } from "@mantine/core";
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { EmptyState, ErrorState, LoadingState } from "../../lib/states";
import { useRecords } from "./hooks";
import {
  clearRecordCursor,
  parseRecordUrlState,
  pushRecordCursor,
  replaceRecordCriteria,
  type RecordUrlState,
} from "./recordUrlState";
import { RecordFilters } from "./RecordFilters";
import { RecordsTable } from "./RecordsTable";

export function RecordsPage() {
  const [params, setParams] = useSearchParams();
  const state = parseRecordUrlState(params);
  const { cursor, ...criteria } = state;
  const isFiltered = Object.keys(criteria).length > 0;
  const records = useRecords({ limit: 50, ...state });
  const setCriteria = useCallback(
    (criteria: Omit<RecordUrlState, "cursor">) => {
      setParams(replaceRecordCriteria(params, criteria), { replace: true });
    },
    [params, setParams],
  );
  const clearAll = useCallback(
    () => setParams(replaceRecordCriteria(params, {}), { replace: true }),
    [params, setParams],
  );
  const invalidCursor =
    Boolean(cursor) &&
    records.error instanceof ApiError &&
    records.error.status === 422 &&
    records.error.code === "validation_error" &&
    records.error.problem?.title === "Invalid records cursor";
  return (
    <Container size="xl" py="md">
      <Title order={2} mb="md">
        Records
      </Title>
      <RecordFilters value={state} onChange={setCriteria} onClear={clearAll} />
      {records.isLoading && <LoadingState label="Loading records" />}
      {invalidCursor && (
        <Alert color="gray" title="This records page is no longer available" mt="md">
          <Button
            component="button"
            variant="light"
            mih={44}
            onClick={() => setParams(clearRecordCursor(params), { replace: true })}
          >
            Return to first page
          </Button>
        </Alert>
      )}
      {records.isError && !invalidCursor && (
        <ErrorState title="Couldn't load records" onRetry={() => records.refetch()} />
      )}
      {records.data && !records.isError && (
        <>
          {records.data.data.length === 0 ? (
            <Alert
              color="gray"
              title={isFiltered ? "No records match your filters" : "No records yet"}
              mt="md"
            >
              <EmptyState
                message={
                  isFiltered
                    ? "Try clearing one or more filters."
                    : "No records have been captured yet."
                }
              />
            </Alert>
          ) : (
            <>
              <RecordsTable records={records.data.data} />
              <Group justify="space-between" mt="sm">
                <span aria-live="polite">{records.data.page.returned} records returned</span>
                {records.data.page.next_cursor && (
                  <Button
                    mih={44}
                    style={{ background: "var(--es-accent-active)", color: "var(--es-on-accent)" }}
                    onClick={() =>
                      setParams(pushRecordCursor(params, records.data!.page.next_cursor!), {
                        replace: false,
                      })
                    }
                    aria-label="Next records page"
                  >
                    Next
                  </Button>
                )}
              </Group>
            </>
          )}
        </>
      )}
    </Container>
  );
}
