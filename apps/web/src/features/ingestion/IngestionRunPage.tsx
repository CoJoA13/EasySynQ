import { Alert, Anchor, Container, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { LoadingState, NoAccessState } from "../../lib/states";
import { CommitProgress } from "./CommitProgress";
import { ReviewCockpit } from "./ReviewCockpit";
import { RunTerminalSummary } from "./RunTerminalSummary";
import { ScanProgress } from "./ScanProgress";
import { useCancelRun, useCommitRun, useImportRun } from "./hooks";

// The human-paced rest states (review cockpit) + the commit + terminal states. Anything NOT in this
// set (Created/Scanning/Extracting/… and any additive stage) is "the engine is still settling" →
// ScanProgress. The switch is exhaustive-by-fallthrough so an unknown additive status degrades calmly.
const REVIEW_STATES = new Set(["Proposed", "Reviewing"]);
const TERMINAL_STATES = new Set(["Completed", "PartiallyCommitted", "Failed", "Cancelled"]);

// S-ing-4b: the four-faces controller for /ingestion/:runId. Reads the run, polls it while settling
// (useImportRun owns the refetchInterval), and mounts exactly one lifecycle face by status. Per-view
// permission is the server's job (403 → calm); a foreign/missing run is a 404 → calm. Selection/queue
// state lives in ReviewCockpit, not here.
export function IngestionRunPage() {
  const { runId = null } = useParams();
  const { data: run, isLoading, isError, error } = useImportRun(runId);
  const cancelRun = useCancelRun(runId);
  const commitRun = useCommitRun(runId);
  const { can } = usePermissions();

  if (isLoading && !run) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading import run" />
      </Container>
    );
  }

  if (isError || !run) {
    const forbidden = error instanceof ApiError && error.status === 403;
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Import review
        </Title>
        {forbidden ? (
          <NoAccessState message="You don't have access to import review." />
        ) : (
          <Alert color="gray" title="Not found">
            Import run not found.{" "}
            <Anchor component={Link} to="/ingestion">
              Back to imports
            </Anchor>
          </Alert>
        )}
      </Container>
    );
  }

  const status = run.status;
  if (REVIEW_STATES.has(status)) {
    return <ReviewCockpit runId={run.id} run={run} />;
  }
  if (status === "Committing") {
    return <CommitProgress run={run} />;
  }
  if (TERMINAL_STATES.has(status)) {
    // PartiallyCommitted → resume is an idempotent re-commit (already-landed items are skipped). Gate
    // the affordance on import.commit (RunTerminalSummary hides Resume when onResume is undefined).
    return (
      <RunTerminalSummary
        run={run}
        onResume={can("import.commit") ? () => commitRun.mutate() : undefined}
      />
    );
  }
  // pre-Proposed (Created/Scanning/Extracting/Classifying/… ) and any additive stage → scan progress.
  // Cancel is gated on import.execute (ScanProgress hides the button when onCancel is undefined).
  return (
    <ScanProgress
      run={run}
      onCancel={can("import.execute") ? () => cancelRun.mutate() : undefined}
    />
  );
}
