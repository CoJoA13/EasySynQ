import { Alert, Anchor, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import type { Finding, FindingType, NcSeverity } from "../../lib/types";
import { useCreateFinding } from "./mutations";

const TYPE_OPTIONS = [
  { value: "NC", label: "NC" },
  { value: "OBSERVATION", label: "Observation" },
  { value: "OFI", label: "OFI" },
];
const SEVERITY_OPTIONS = ["Critical", "Major", "Minor"].map((s) => ({ value: s, label: s }));

// An NC REQUIRES a severity (the backend 422s; the auto-CAPA needs one) — the confirm stays
// disabled until picked (the 7c SpawnCapaModal no-dead-end lesson). An NC success shows the
// auto-created CAPA confirmation + deep-link instead of silently closing.
export function LogFindingModal({
  auditId,
  opened,
  onClose,
}: {
  auditId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [type, setType] = useState<FindingType | null>(null);
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [summary, setSummary] = useState("");
  const [clauseRef, setClauseRef] = useState("");
  const [processRef, setProcessRef] = useState("");
  const [created, setCreated] = useState<Finding | null>(null);
  const create = useCreateFinding(auditId);

  const ncWithoutSeverity = type === "NC" && severity === null;

  function submit() {
    if (!type || ncWithoutSeverity) return;
    create.mutate(
      {
        finding_type: type,
        ...(severity ? { severity } : {}),
        ...(summary.trim() ? { summary: summary.trim() } : {}),
        ...(clauseRef.trim() ? { clause_ref: clauseRef.trim() } : {}),
        ...(processRef.trim() ? { process_ref: processRef.trim() } : {}),
      },
      {
        onSuccess: (f) => {
          if (f.auto_capa_id) setCreated(f); // NC → show the CAPA confirmation
          else onClose();
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Log finding" closeButtonProps={{ "aria-label": "Close log finding dialog" }}>
      {created ? (
        <Stack gap="sm">
          <Alert color="green" title="Finding logged">
            <Text size="sm" mb="xs">
              {created.identifier} — CAPA auto-created for this NC.
            </Text>
            <Anchor component={Link} to={`/capa?capa=${created.auto_capa_id}`}>
              View CAPA →
            </Anchor>
          </Alert>
          <Group justify="flex-end">
            <Button onClick={onClose}>Done</Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap="sm">
          <Select label="Type" required data={TYPE_OPTIONS} value={type} onChange={(v) => setType(v as FindingType | null)} />
          <Select
            label={type === "NC" ? "Severity (required for an NC)" : "Severity"}
            data={SEVERITY_OPTIONS}
            value={severity}
            onChange={(v) => setSeverity(v as NcSeverity | null)}
            clearable
          />
          <TextInput label="Summary" maxLength={300} value={summary} onChange={(e) => setSummary(e.currentTarget.value)} />
          <TextInput label="Clause ref" placeholder="e.g. 8.4" value={clauseRef} onChange={(e) => setClauseRef(e.currentTarget.value)} />
          <TextInput label="Process ref" value={processRef} onChange={(e) => setProcessRef(e.currentTarget.value)} />
          {create.isError && (
            <Alert color="red" title="Couldn't log the finding">
              {create.error instanceof ApiError ? create.error.message : "Please try again."}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!type || ncWithoutSeverity} loading={create.isPending}>
              Log finding
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
