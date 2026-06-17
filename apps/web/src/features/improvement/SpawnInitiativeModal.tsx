import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import type { UseMutationResult } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { Initiative, InitiativeSpawnBody } from "../../lib/types";
import { useProcesses } from "../objectives/hooks";
import type { SpawnInitiativeVars } from "./mutations";

// Raise an initiative FROM an ISO origin (an OFI/OBSERVATION finding or an ACTION/IMPROVEMENT MR output).
// Generic over the bound spawn mutation (the SpawnDcrModal precedent): the parent calls the hook top-level
// and passes the mutation; 201-new / 200-replay both resolve to an Initiative (no status branching). A fresh
// per-mount Idempotency-Key dedups a double-submit. Title is the only required field; owner is always
// optional, and the process picker is offered ONLY for the MR seam (showProcessPicker) — the finding seam
// derives the process from the audit server-side. Conditionally mounted by the parent so close = unmount +
// reset + a fresh key.
export function SpawnInitiativeModal({
  heading,
  mutation,
  showProcessPicker = false,
  onClose,
  onCreated,
}: {
  heading: string;
  mutation: UseMutationResult<Initiative, Error, SpawnInitiativeVars>;
  showProcessPicker?: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { data: directory } = useUserDirectory();
  const { data: processes } = useProcesses();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [targetOutcome, setTargetOutcome] = useState("");
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [processId, setProcessId] = useState<string | null>(null);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);
  const canSubmit = title.trim() !== "";

  async function submit() {
    setError(null);
    if (!canSubmit) return;
    const body: InitiativeSpawnBody = {
      title: title.trim(),
      description: description.trim() === "" ? null : description.trim(),
      target_outcome: targetOutcome.trim() === "" ? null : targetOutcome.trim(),
      owner_user_id: ownerId,
      // process_id is sent ONLY for the MR seam (the finding endpoint derives it from the audit).
      ...(showProcessPicker ? { process_id: processId } : {}),
    };
    try {
      const created = await mutation.mutateAsync({ body, idempotencyKey });
      onCreated(created.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the initiative.");
    }
  }

  return (
    // `opened` is hardcoded true — the parent conditionally mounts this modal, so close = unmount + reset.
    <Modal opened onClose={onClose} title={heading} size="lg">
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
        {showProcessPicker && processes && processes.length > 0 && (
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
          {/* "Raise" (not "Raise initiative") so the submit doesn't share an accessible name with the
              caller's "Raise initiative" trigger button (the SpawnDcrModal precedent). */}
          <Button onClick={() => void submit()} loading={mutation.isPending} disabled={!canSubmit}>
            Raise
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
