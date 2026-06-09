// apps/web/src/features/capa/EvidenceLinker.tsx
import { Alert, Button, Group, Select, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useRecords } from "./hooks";
import { useLinkEvidence } from "./mutations";

// Light "link an existing record as evidence" affordance (epic §7: no net-new upload). The picked record
// is linked to THIS capa_stage (target_type=capa_stage) — the M4 close gate needs ≥1 link on the
// current-cycle Implement + Verify stages.
export function EvidenceLinker({ capaId, stageId }: { capaId: string; stageId: string }) {
  const { data: records } = useRecords();
  const link = useLinkEvidence(capaId);
  const [recordId, setRecordId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit() {
    if (!recordId) return;
    setError(null);
    try {
      await link.mutateAsync({ recordId, targetId: stageId, linkReason: reason.trim() || undefined });
      setDone(true);
      setRecordId(null);
      setReason("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not link the record.");
    }
  }

  return (
    <div>
      {error && (
        <Alert color="red" mb="xs" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {done && (
        <Text size="xs" c="teal" mb="xs">
          Linked.
        </Text>
      )}
      <Group align="flex-end" gap="xs">
        <Select
          label="Record"
          placeholder="Pick a record"
          searchable
          value={recordId}
          onChange={setRecordId}
          comboboxProps={{ keepMounted: false }}
          data={(records ?? []).map((r) => ({
            value: r.id,
            label: `${r.identifier ?? r.id} — ${r.title}`,
          }))}
        />
        <TextInput
          aria-label="Link reason"
          placeholder="Reason (optional)"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
        />
        <Button onClick={() => void submit()} loading={link.isPending} disabled={!recordId}>
          Link evidence
        </Button>
      </Group>
    </div>
  );
}
