import { Alert, Button, Card, Group, Loader, Stepper, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// The pre-Proposed "watch" face (§3 step 2): a calm stepper of the auto-chained pipeline stages with
// the current stage highlighted + a Cancel (import.execute). The run page polls run.status (the
// useImportRun refetchInterval) and re-renders this until the run rests at Proposed or Failed. A
// Failed run is a calm Alert with run.error — never a thrown error or a red crash (DP-6). Stages
// beyond the known set (additive engine stages) degrade to a generic "Working…" rather than crash.

// Ordered pipeline stages → the stepper rows. `status` maps to the active step; a "*-ed" rest status
// (Scanned/Classified/…) sits between two stages and reads as the later one being in flight.
const STAGES: { key: string; label: string; caption: string }[] = [
  { key: "scan", label: "Scanning files", caption: "Scanning the source folder…" },
  { key: "extract", label: "Reading text", caption: "Extracting text (and OCR where enabled)…" },
  { key: "classify", label: "Classifying content", caption: "Classifying kind, type, and clauses…" },
  { key: "dedup", label: "Finding duplicates", caption: "Grouping duplicates and version families…" },
  { key: "propose", label: "Proposing a plan", caption: "Proposing identifiers and placement…" },
];

const STATUS_TO_STEP: Record<string, number> = {
  Created: 0,
  Scanning: 0,
  Scanned: 1,
  Extracting: 1,
  Classifying: 2,
  Classified: 3,
  Deduping: 3,
  Proposing: 4,
  Proposed: 4,
};

export function ScanProgress({ run, onCancel }: { run: ImportRun; onCancel: () => void }) {
  if (run.status === "Failed") {
    return (
      <Card withBorder padding="lg">
        <Alert color="gray" title="The import couldn't finish scanning">
          {run.error ?? "The engine stopped before proposing a plan. You can start a new import."}
        </Alert>
      </Card>
    );
  }

  // A KNOWN status maps to a stage; an unknown/additive status (a future engine stage) has no stage →
  // render the generic "Working…" rather than mislabel it as the first scan stage.
  const known = run.status in STATUS_TO_STEP;
  const step = STATUS_TO_STEP[run.status] ?? 0;
  const current = known ? (STAGES[step] ?? null) : null;
  const caption = current?.caption ?? "Please wait…";

  return (
    <Card withBorder padding="lg">
      <Group justify="space-between" mb="md" wrap="nowrap">
        <Group gap="sm" wrap="nowrap">
          <Loader size="sm" aria-hidden="true" />
          <Text fw={600}>
            {current ? current.label : "Working…"}
          </Text>
        </Group>
        <Button variant="default" onClick={onCancel} aria-label="Cancel import">
          Cancel import
        </Button>
      </Group>
      <Stepper active={step} size="sm" aria-label="Import pipeline progress">
        {STAGES.map((s, i) => (
          <Stepper.Step key={s.key} label={s.label} aria-label={`Stage ${i + 1}: ${s.label}`} />
        ))}
      </Stepper>
      <Text c="dimmed" size="sm" mt="md">
        {caption} — Scanning… nothing touches the vault yet.
      </Text>
    </Card>
  );
}
