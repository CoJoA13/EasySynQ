import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Initiative, InitiativeStage } from "../../lib/types";
import { useTransitionInitiative } from "./mutations";

// A transition that requires an explanation (Cancel / Close). The server 422s a Closed/Cancelled move
// with a blank comment, so the confirm button disables until a non-blank comment is present (the DCR
// CancelDcrModal's comment is OPTIONAL with no such validation — added here per the FSM rule). A Close
// move additionally folds an optional realized-benefit `outcome` into the sealed stage event (10.3).
export function TransitionModal({
  initiative,
  toState,
  title,
  description,
  confirmLabel,
  confirmColor,
  withOutcome = false,
  onClose,
}: {
  initiative: Initiative;
  toState: InitiativeStage;
  title: string;
  description: string;
  confirmLabel: string;
  confirmColor?: string;
  withOutcome?: boolean;
  onClose: () => void;
}) {
  const m = useTransitionInitiative(initiative.id);
  const [comment, setComment] = useState("");
  const [outcome, setOutcome] = useState("");
  const [error, setError] = useState<string | null>(null);
  const commentOk = comment.trim() !== "";

  async function submit() {
    setError(null);
    if (!commentOk) return;
    try {
      await m.mutateAsync({
        to_state: toState,
        comment: comment.trim(),
        outcome: withOutcome && outcome.trim() !== "" ? outcome.trim() : undefined,
      });
      onClose();
    } catch (e) {
      // A 409 improvement_transition_invalid (concurrent advance) is calm; onSettled refreshes the drawer.
      setError(e instanceof ApiError ? e.message : "Could not update the initiative.");
    }
  }

  return (
    <Modal opened onClose={onClose} title={title}>
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">{description}</Text>
        <Textarea
          label="Comment"
          required
          autosize
          minRows={2}
          value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
        />
        {withOutcome && (
          <Textarea
            label="Realized benefit (optional)"
            autosize
            minRows={2}
            value={outcome}
            onChange={(e) => setOutcome(e.currentTarget.value)}
          />
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Keep open
          </Button>
          <Button
            color={confirmColor}
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!commentOk}
          >
            {confirmLabel}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
