import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { InitiativeCreateBody } from "../../lib/types";
import { useProcesses } from "../objectives/hooks";
import { useCreateInitiative } from "./mutations";

// Manually raise an initiative (POST, source=manual). Born at Open. Title is the only required field;
// process/owner are optional pickers (degrade/omit on a 403 directory/process read). Conditionally
// mounted by the parent so close unmounts + resets state.
export function RaiseInitiativeModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useCreateInitiative();
  const { data: directory } = useUserDirectory();
  const { data: processes } = useProcesses();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [targetOutcome, setTargetOutcome] = useState("");
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [processId, setProcessId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = title.trim() !== "";

  async function submit() {
    setError(null);
    if (!canSubmit) return;
    const body: InitiativeCreateBody = {
      title: title.trim(),
      description: description.trim() === "" ? null : description.trim(),
      target_outcome: targetOutcome.trim() === "" ? null : targetOutcome.trim(),
      process_id: processId,
      owner_user_id: ownerId,
    };
    try {
      const created = await m.mutateAsync(body);
      onCreated(created.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the initiative.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Raise improvement initiative" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput
          label="Title"
          required
          placeholder="What is the improvement?"
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
          placeholder="The benefit you expect to realize"
          value={targetOutcome}
          onChange={(e) => setTargetOutcome(e.currentTarget.value)}
        />
        {processes && processes.length > 0 && (
          <Select
            label="Process (optional)"
            clearable
            value={processId}
            onChange={setProcessId}
            data={processes.map((p) => ({ value: p.id, label: p.name }))}
            comboboxProps={{ keepMounted: false }}
          />
        )}
        {directory && directory.length > 0 && (
          <Select
            label="Owner (optional)"
            clearable
            searchable
            value={ownerId}
            onChange={setOwnerId}
            data={directory.map((u) => ({ value: u.id, label: u.display_name ?? u.id }))}
            comboboxProps={{ keepMounted: false }}
          />
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!canSubmit}>
            Raise initiative
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
