import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useCloseDcr } from "./mutations";

// Submit-and-show — the close gate (the change must have taken effect) is a SERVER-only truth, so we
// don't pre-gate the button. On a 409 dcr_effectivity_pending the server's message lists exactly what's
// pending (one of three: retirement not yet effective / no resulting version linked / version not yet
// Effective — cutover pending). The onSettled invalidate refreshes the drawer behind the calm error.
export function CloseDcrAction({ dcrId }: { dcrId: string }) {
  const m = useCloseDcr(dcrId);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError(e.message);
      else setError(e instanceof ApiError ? e.message : "Something went wrong. Please retry.");
    }
  }

  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      <Text size="sm" c="dimmed">
        Closing requires the change to have taken effect (the resulting version Effective, or the
        target Obsolete). The server confirms and reports anything pending.
      </Text>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending}>
          Close change request
        </Button>
      </Group>
    </Stack>
  );
}
