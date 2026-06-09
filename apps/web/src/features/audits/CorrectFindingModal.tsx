import { Alert, Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Finding, FindingType, NcSeverity } from "../../lib/types";
import { useCorrectFinding } from "./mutations";

const TYPE_OPTIONS = [
  { value: "NC", label: "NC" },
  { value: "OBSERVATION", label: "Observation" },
  { value: "OFI", label: "OFI" },
];
const SEVERITY_OPTIONS = ["Critical", "Major", "Minor"].map((s) => ({ value: s, label: s }));

// Correct-don't-edit: a retype in ANY direction captures a superseding successor. Pre-filled from
// the original; a retype TO NC requires a severity (422 otherwise — disabled until picked).
export function CorrectFindingModal({
  finding,
  auditId,
  opened,
  onClose,
}: {
  finding: Finding;
  auditId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [type, setType] = useState<FindingType>(finding.finding_type);
  const [severity, setSeverity] = useState<NcSeverity | null>(finding.severity);
  const [clauseRef, setClauseRef] = useState(finding.clause_ref ?? "");
  const [processRef, setProcessRef] = useState(finding.process_ref ?? "");
  const [reason, setReason] = useState("");
  const correct = useCorrectFinding(auditId);

  const ncWithoutSeverity = type === "NC" && severity === null;

  function submit() {
    if (ncWithoutSeverity) return;
    correct.mutate(
      {
        findingId: finding.id,
        body: {
          finding_type: type,
          ...(severity ? { severity } : {}),
          ...(clauseRef.trim() ? { clause_ref: clauseRef.trim() } : {}),
          ...(processRef.trim() ? { process_ref: processRef.trim() } : {}),
          ...(reason.trim() ? { reason: reason.trim() } : {}),
        },
      },
      { onSuccess: onClose },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Correct ${finding.identifier ?? "finding"}`} closeButtonProps={{ "aria-label": "Close correct finding dialog" }}>
      <Stack gap="sm">
        <Select label="Type" required data={TYPE_OPTIONS} value={type} onChange={(v) => v && setType(v as FindingType)} />
        <Select
          label={type === "NC" ? "Severity (required for an NC)" : "Severity"}
          data={SEVERITY_OPTIONS}
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          clearable
        />
        <TextInput label="Clause ref" value={clauseRef} onChange={(e) => setClauseRef(e.currentTarget.value)} />
        <TextInput label="Process ref" value={processRef} onChange={(e) => setProcessRef(e.currentTarget.value)} />
        <TextInput label="Reason" maxLength={300} value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
        {correct.isError && (
          <Alert color="red" title="Couldn't correct the finding">
            {correct.error instanceof ApiError ? correct.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={ncWithoutSeverity} loading={correct.isPending}>
            Save correction
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
