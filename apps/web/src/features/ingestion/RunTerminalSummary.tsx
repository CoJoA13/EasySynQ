import { Alert, Anchor, Button, Card, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { ImportRun } from "../../lib/types";

// The terminal face (§3 step 5), status-aware:
//   Completed          → "Import complete" + committed/failed counts + a Library link.
//   PartiallyCommitted → "Import partially committed" + counts + a calm Resume affordance + a Library
//                        link (resume re-commits the remaining subset; already-landed items are skipped).
//   Failed             → "Import failed" + run.error in a calm Alert. NO commit counts, NO Library link.
//   Cancelled          → "Import cancelled" — a calm note. NO counts, NO Library link.
// All four hops into run.counts.commit degrade to 0 (noUncheckedIndexedAccess). DP-6: a partial/failed
// run is a calm summary, never a red crash. There is no /records/:id route, so the Import Report
// surfaces as a calm text note (the captured record id), not a broken link.
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
  const failed = run.status === "Failed";
  const cancelled = run.status === "Cancelled";
  const partial = run.status === "PartiallyCommitted";

  // Failed / Cancelled are calm, count-less, link-less faces (a Failed run never committed a subset).
  if (failed) {
    return (
      <Card withBorder padding="lg">
        <Stack gap="md">
          <Text fw={700} size="lg">
            Import failed
          </Text>
          <Alert color="gray" title="The import couldn't be committed">
            {run.error ?? "The commit stopped before any item became controlled. You can start a new import."}
          </Alert>
        </Stack>
      </Card>
    );
  }

  if (cancelled) {
    return (
      <Card withBorder padding="lg">
        <Stack gap="md">
          <Text fw={700} size="lg">
            Import cancelled
          </Text>
          <Text c="dimmed" size="sm">
            This import was cancelled before anything became controlled. Nothing touched the vault.
          </Text>
        </Stack>
      </Card>
    );
  }

  const committed = commitCount(run.counts, "committed");
  const failedCount = commitCount(run.counts, "failed");
  const heading = partial ? "Import partially committed" : "Import complete";

  return (
    <Card withBorder padding="lg">
      <Stack gap="md">
        <Text fw={700} size="lg">
          {heading}
        </Text>
        <Group gap="lg">
          <Text aria-label={`Committed: ${committed}`}>✓ {committed} committed</Text>
          <Text c="dimmed" aria-label={`Failed: ${failedCount}`}>
            ✕ {failedCount} failed
          </Text>
        </Group>

        {run.report_record_id ? (
          <Text c="dimmed" size="sm">
            Import Report captured — record {run.report_record_id}
          </Text>
        ) : (
          <Text c="dimmed" size="sm">
            The Import report isn't available for this run.
          </Text>
        )}

        {partial && (
          <Alert color="gray" title="Some items weren't committed">
            <Stack gap="sm">
              <Text size="sm">
                {failedCount} item{failedCount === 1 ? "" : "s"} couldn&rsquo;t be committed. Resuming re-attempts
                the remaining subset; items already in the vault are skipped.
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
