// apps/web/src/features/capa/EvidenceLinker.tsx
import { Alert, Button, Group, Select, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useRecords } from "./hooks";
import { useLinkEvidence } from "./mutations";

// Light "link an existing record as evidence" affordance (epic §7: no net-new upload). The picked record
// is linked to THIS capa_stage (target_type=capa_stage) — the M4 close gate needs ≥1 link on the
// current-cycle Implement + Verify stages.
export function EvidenceLinker({
  capaId,
  stageId,
  labelSuffix = "",
}: {
  capaId: string;
  stageId: string;
  // Appended to the "Record"/"Reason" labels so two linkers on one screen (Implement + Verify stages)
  // don't share an accessible name (the S-web-6 duplicate-getByLabelText trap). Default "" keeps the
  // standalone labels plain.
  labelSuffix?: string;
}) {
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
          label={`Record${labelSuffix}`}
          placeholder="Pick a record"
          searchable
          value={recordId}
          onChange={(v) => {
            // A new selection means a new (not-yet-submitted) link — drop the prior "Linked."/error so
            // the banners describe the CURRENT selection, not the last one.
            setRecordId(v);
            setDone(false);
            setError(null);
          }}
          comboboxProps={{ keepMounted: false }}
          data={(records ?? []).map((r) => ({
            value: r.id,
            label: `${r.identifier ?? r.id} — ${r.title}`,
          }))}
        />
        <TextInput
          label={`Reason${labelSuffix}`}
          placeholder="Optional"
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
