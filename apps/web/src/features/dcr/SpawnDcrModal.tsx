import { Alert, Button, Group, Modal, Stack } from "@mantine/core";
import type { UseMutationResult } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import type { Dcr, DcrSpawnBody } from "../../lib/types";
import {
  DcrRaiseFields,
  EMPTY_DCR_FIELDS,
  isDcrFieldsValid,
  proposedEffectiveIso,
  type DcrFieldsValue,
} from "./DcrRaiseFields";
import type { SpawnDcrVars } from "./mutations";

// Parameterized for both spawn seams (CAPA + MR-output). The parent calls the hook (top-level) and passes
// the mutation; both resolve a Dcr identically for 201-new / 200-replay (no status branching). Conditionally
// mounted by the parent so close unmounts + resets. A fresh per-mount Idempotency-Key dedups a double-submit.
export function SpawnDcrModal({
  title,
  mutation,
  onClose,
}: {
  title: string;
  mutation: UseMutationResult<Dcr, Error, SpawnDcrVars>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [fields, setFields] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!isDcrFieldsValid(fields)) return;
    const body: DcrSpawnBody = {
      change_type: fields.change_type,
      change_significance: fields.change_significance,
      reason_text: fields.reason_text.trim(),
      target_document_id: fields.change_type === "CREATE" ? null : fields.target_document_id,
      proposed_effective_from: proposedEffectiveIso(fields.proposed_effective_from),
    };
    try {
      const dcr = await mutation.mutateAsync({ body, idempotencyKey });
      navigate(`/dcrs?dcr=${dcr.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <DcrRaiseFields value={fields} onChange={setFields} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={mutation.isPending} disabled={!isDcrFieldsValid(fields)}>
            Raise change request
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
