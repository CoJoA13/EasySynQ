import { Alert, Button, Group, Modal, Select, Stack } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrCreateBody, DcrReasonClass } from "../../lib/types";
import {
  DcrRaiseFields,
  EMPTY_DCR_FIELDS,
  isDcrFieldsValid,
  proposedEffectiveIso,
  type DcrFieldsValue,
} from "./DcrRaiseFields";
import { REASON_LABEL } from "./labels";
import { useRaiseDcr } from "./mutations";

// Conditionally mounted by the parent ({raising && <RaiseDcrModal/>}) so close unmounts + resets state.
export function RaiseDcrModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useRaiseDcr();
  const [fields, setFields] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  const [reasonClass, setReasonClass] = useState<DcrReasonClass | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!reasonClass || !isDcrFieldsValid(fields)) return;
    const body: DcrCreateBody = {
      change_type: fields.change_type,
      change_significance: fields.change_significance,
      reason_class: reasonClass,
      reason_text: fields.reason_text.trim(),
      target_document_id: fields.change_type === "CREATE" ? null : fields.target_document_id,
      proposed_effective_from: proposedEffectiveIso(fields.proposed_effective_from),
    };
    try {
      const dcr = await m.mutateAsync(body);
      onCreated(dcr.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Raise change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <DcrRaiseFields value={fields} onChange={setFields} />
        <Select
          label="Reason class"
          required
          placeholder="Pick a reason"
          value={reasonClass}
          onChange={(v) => setReasonClass(v as DcrReasonClass)}
          data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(([value, label]) => ({
            value,
            label,
          }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!reasonClass || !isDcrFieldsValid(fields)}
          >
            Raise
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
