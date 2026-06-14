import { Alert, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ChangeSignificance } from "../../lib/types";
import { useRouteDcr } from "./mutations";

// Conditionally mounted by DcrAdvancePanel (state===Assessed). A confirm — routing instantiates the
// approval workflow + notifies approvers, and route() lands the DCR directly in InApproval (the
// Assessed→Routed→InApproval double-hop is server-atomic; there is no observable Routed rest-state).
export function RouteDcrModal({
  dcrId,
  significance,
  onClose,
}: {
  dcrId: string;
  significance: ChangeSignificance;
  onClose: () => void;
}) {
  const m = useRouteDcr(dcrId);
  const [error, setError] = useState<string | null>(null);
  const route = significance === "MAJOR" ? "Process Owner, then QMS Owner" : "QMS Owner";

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync();
      onClose();
    } catch (e) {
      // 409 dcr_no_approvers / dcr_not_routable / dcr_approval_in_progress — the server's word; the
      // onSettled invalidate refreshes the drawer to the real state behind this calm error.
      setError(e instanceof ApiError ? e.message : "Could not route the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Route for approval">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">
          This spins up the approval workflow and notifies the assigned approver(s) ({route}). It
          can&apos;t be edited once routed.
        </Text>
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Not yet
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending}>
            Route for approval
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
