import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Initiative } from "../../lib/types";
import { useRequestInitiativeAuthorization } from "./mutations";

// S-improvement-4: the manager asks Top Management to authorize closing a Completed initiative. The
// optional comment is a justification for the request (the binding leadership note is the verifier's
// own comment at sign time). On success the cockpit refetches the authorization cycle and shows
// "awaiting sign-off". A 409 (not Completed / already in flight) is rendered calmly.
export function RequestAuthorizationModal({
  initiative,
  onClose,
}: {
  initiative: Initiative;
  onClose: () => void;
}) {
  const request = useRequestInitiativeAuthorization(initiative.id);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await request.mutateAsync({ comment: comment.trim() || null });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not request authorization.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Request management authorization">
      <Stack gap="md">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">
          Route {initiative.identifier} to Top Management for a signed authorization. The initiative
          closes only once a Top-Management member verifies the realized benefit and signs.
        </Text>
        <Textarea
          label="Note for Top Management"
          description="Optional — why this improvement is ready to be authorized."
          value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={request.isPending}>
            Request authorization
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
