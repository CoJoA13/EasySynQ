import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { Initiative, InitiativePatchBody } from "../../lib/types";
import { useProcesses } from "../objectives/hooks";
import { usePatchInitiative } from "./mutations";

// Edit mutable metadata (PATCH; never the stage). Per the backend contract null/absent = UNCHANGED, so
// this modal reassigns owner/process and rewrites text, but a blanked field is left unchanged (it
// cannot CLEAR a field — a documented v1 limitation mirroring the backend "never clears a field").
// Owner/process are reassign-only Selects for the same reason.
export function EditInitiativeModal({
  initiative,
  onClose,
}: {
  initiative: Initiative;
  onClose: () => void;
}) {
  const m = usePatchInitiative(initiative.id);
  const { data: directory } = useUserDirectory();
  const { data: processes } = useProcesses();
  const [title, setTitle] = useState(initiative.title);
  const [description, setDescription] = useState(initiative.description ?? "");
  const [targetOutcome, setTargetOutcome] = useState(initiative.target_outcome ?? "");
  const [ownerId, setOwnerId] = useState<string | null>(initiative.owner_user_id);
  const [processId, setProcessId] = useState<string | null>(initiative.process_id);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = title.trim() !== "";

  async function submit() {
    setError(null);
    if (!canSubmit) return;
    // Blank text → undefined (leave unchanged), never "" — honouring the backend's no-clear contract.
    const body: InitiativePatchBody = {
      title: title.trim(),
      description: description.trim() === "" ? undefined : description.trim(),
      target_outcome: targetOutcome.trim() === "" ? undefined : targetOutcome.trim(),
      owner_user_id: ownerId ?? undefined,
      process_id: processId ?? undefined,
    };
    try {
      await m.mutateAsync(body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the initiative.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Edit initiative">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <Textarea
          label="Description"
          autosize
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Textarea
          label="Target outcome"
          autosize
          minRows={2}
          value={targetOutcome}
          onChange={(e) => setTargetOutcome(e.currentTarget.value)}
        />
        {directory && directory.length > 0 && (
          <Select
            label="Owner"
            placeholder="Reassign owner"
            value={ownerId}
            onChange={setOwnerId}
            data={directory.map((u) => ({ value: u.id, label: u.display_name ?? u.id }))}
            comboboxProps={{ keepMounted: false }}
            searchable
          />
        )}
        {processes && processes.length > 0 && (
          <Select
            label="Process"
            placeholder="Reassign process"
            value={processId}
            onChange={setProcessId}
            data={processes.map((p) => ({ value: p.id, label: p.name }))}
            comboboxProps={{ keepMounted: false }}
          />
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!canSubmit}>
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
