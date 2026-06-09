import { Alert, Anchor, Button, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { ImportRun } from "../../lib/types";

// The terminal face (§3 step 5): committed/failed tallies, a link to the Import Report record (when
// run.report_record_id is set), and — for PartiallyCommitted — a calm "Resume commit" affordance
// (the page wires onResume to useCommitRun; idempotent re-commit is a no-op for the already-landed
// subset). All four hops into run.counts.commit degrade to 0 (noUncheckedIndexedAccess). DP-6: a
// partial run is a calm summary, never an error.
function commitCount(counts: ImportRun["counts"], key: "committed" | "failed"): number {
  if (!counts || typeof counts !== "object") return 0;
  const commit = (counts as Record<string, unknown>)["commit"];
  if (!commit || typeof commit !== "object") return 0;
  const value = (commit as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

export function RunTerminalSummary({
  run,
  onResume,
}: {
  run: ImportRun;
  onResume?: () => void;
}) {
  const committed = commitCount(run.counts, "committed");
  const failed = commitCount(run.counts, "failed");
  const partial = run.status === "PartiallyCommitted";
  const heading = partial ? "Import partially committed" : "Import complete";

  return (
    <Card withBorder padding="lg">
      <Stack gap="md">
        <Text fw={700} size="lg">
          {heading}
        </Text>
        <Group gap="lg">
          <Text aria-label={`Committed: ${committed}`}>✓ {committed} committed</Text>
          <Text c="dimmed" aria-label={`Failed: ${failed}`}>
            ✕ {failed} failed
          </Text>
        </Group>

        {run.report_record_id ? (
          <Anchor component={Link} to={`/records/${run.report_record_id}`}>
            View the Import Report record →
          </Anchor>
        ) : (
          <Text c="dimmed" size="sm">
            The Import report isn't available for this run.
          </Text>
        )}

        {partial && (
          <Alert color="gray" title="Some items weren't committed">
            <Stack gap="sm">
              <Text size="sm">
                {failed} item{failed === 1 ? "" : "s"} couldn&rsquo;t be committed. Resuming re-attempts the
                remaining subset; items already in the vault are skipped.
              </Text>
              {onResume && (
                <Group>
                  <Button onClick={onResume}>Resume commit</Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}

        <Group>
          <Anchor component={Link} to="/library">
            View the committed documents in the Library →
          </Anchor>
        </Group>
      </Stack>
    </Card>
  );
}
