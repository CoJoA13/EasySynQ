import { Alert, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDecideMrTask } from "./mrTaskHooks";

// S-mr-2: the MR_ACTION completion. One-click `complete`, NO signature (recording an action's
// completion mints no signature — R43) → this is NOT a DecisionCard: prominent copy + one button,
// mirroring the AttestationCard shape.
const CODE_COPY: Record<string, string> = {
  validation_error: "This action only supports being marked complete.",
  not_found: "This task is no longer assigned to you.",
};

export function MrActionCard({ taskId, reviewId }: { taskId: string; reviewId: string }) {
  const decide = useDecideMrTask();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // One stable key for this mounted attempt (the DecisionCard/AttestationCard pattern): a lost-response
  // retry replays instead of minting a fresh client_token the engine can't match.
  const [idemKey] = useState(() => crypto.randomUUID());

  async function submit() {
    setError(null);
    try {
      await decide.mutateAsync({ taskId, reviewId, idempotencyKey: idemKey });
      navigate("/tasks");
    } catch (e) {
      setError(
        e instanceof ApiError
          ? (CODE_COPY[e.code] ?? e.message)
          : "Something went wrong. Please retry.",
      );
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Mark this management-review action complete</Text>
        <Text size="sm">Completing confirms the tracked action from the review is done.</Text>
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={decide.isPending}>
            Mark action complete
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}
