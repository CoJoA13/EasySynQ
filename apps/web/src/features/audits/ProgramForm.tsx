import { Alert, Button, Group, Modal, Stack, Switch, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { AuditProgram } from "../../lib/types";
import { useCreateProgram, useUpdateProgram } from "./mutations";

// Create (program == null) or edit (pre-filled + the Archived toggle). `coverage` is not exposed
// (free-form dict — no honest form, spec §5).
export function ProgramForm({
  program,
  opened,
  onClose,
}: {
  program: AuditProgram | null;
  opened: boolean;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(program?.title ?? "");
  const [period, setPeriod] = useState(program?.period ?? "");
  const [archived, setArchived] = useState(program?.archived ?? false);
  const create = useCreateProgram();
  const update = useUpdateProgram(program?.id ?? "");
  const active = program ? update : create;

  function submit() {
    if (!title.trim()) return;
    const onSuccess = () => onClose();
    if (program) {
      // Send period only when it actually changed — and send "" when the user CLEARED a set value
      // (omitting it would silently keep the old one; the PATCH treats absent as no-change).
      const periodChanged = period.trim() !== (program.period ?? "");
      update.mutate(
        { title: title.trim(), ...(periodChanged ? { period: period.trim() } : {}), archived },
        { onSuccess },
      );
    } else {
      create.mutate(
        { title: title.trim(), ...(period.trim() ? { period: period.trim() } : {}) },
        { onSuccess },
      );
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={program ? "Edit programme" : "New programme"}>
      <Stack gap="sm">
        <TextInput
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <TextInput
          label="Period"
          placeholder="e.g. 2026"
          value={period}
          onChange={(e) => setPeriod(e.currentTarget.value)}
        />
        {program && (
          <Switch
            label="Archived"
            checked={archived}
            onChange={(e) => setArchived(e.currentTarget.checked)}
          />
        )}
        {active.isError && (
          <Alert color="red" title="Couldn't save the programme">
            {active.error instanceof ApiError ? active.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!title.trim()} loading={active.isPending}>
            Save programme
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
