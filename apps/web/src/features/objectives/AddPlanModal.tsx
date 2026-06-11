import { Alert, Button, Group, Modal, Stack, TextInput, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { PlanCreateBody } from "../../lib/types";
import { useAddPlan } from "./mutations";

interface Props {
  opened: boolean;
  objectiveId: string;
  onClose: () => void;
}

export function AddPlanModal({ opened, objectiveId, onClose }: Props) {
  const add = useAddPlan(objectiveId);
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    const body: PlanCreateBody = {
      action: action.trim(),
      resource: resource.trim() === "" ? null : resource.trim(),
      due_date: dueDate === "" ? null : dueDate,
    };
    try {
      await add.mutateAsync(body);
      setAction(""); setResource(""); setDueDate("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong adding the plan.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Add plan">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea label="Action" required autosize minRows={2} value={action} onChange={(e) => setAction(e.currentTarget.value)} />
        <TextInput label="Resource (optional)" value={resource} onChange={(e) => setResource(e.currentTarget.value)} />
        <TextInput type="date" label="Due date (optional)" value={dueDate} onChange={(e) => setDueDate(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={add.isPending} disabled={action.trim() === ""}>
            Add plan
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
