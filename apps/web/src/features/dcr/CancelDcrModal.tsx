import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrDetail } from "../../lib/types";
import { useCancelDcr } from "./mutations";

export function CancelDcrModal({ dcr, onClose }: { dcr: DcrDetail; onClose: () => void }) {
  const m = useCancelDcr(dcr.id);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ comment: comment.trim() || undefined });
      onClose();
    } catch (e) {
      // 409 dcr_not_cancellable (concurrent advance) — calm; the onSettled invalidate refreshes the drawer.
      setError(e instanceof ApiError ? e.message : "Could not cancel the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Cancel change request">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">This withdraws {dcr.identifier}. It can&apos;t be undone.</Text>
        <Textarea
          label="Comment (optional)"
          autosize
          minRows={2}
          value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Keep open
          </Button>
          <Button color="red" onClick={() => void submit()} loading={m.isPending}>
            Cancel change request
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
