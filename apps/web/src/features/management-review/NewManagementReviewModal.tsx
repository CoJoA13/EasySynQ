import { Alert, Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useCreateReview } from "./mutations";

// The parent owns `{open && <Modal/>}` (close unmounts + resets the modal — the S-web-7d idiom).
export function NewManagementReviewModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const create = useCreateReview();
  const [title, setTitle] = useState("");
  const [period, setPeriod] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canSave = title.trim().length > 0;

  async function submit() {
    setError(null);
    try {
      const mr = await create.mutateAsync({
        title: title.trim(),
        period_label: period.trim() || undefined,
      });
      onCreated(mr.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please retry.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New management review">
      <Stack gap="sm">
        <TextInput
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <TextInput
          label="Period"
          placeholder="2026 Annual"
          value={period}
          onChange={(e) => setPeriod(e.currentTarget.value)}
        />
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSave} loading={create.isPending} onClick={() => void submit()}>
            Create
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
