import { Alert, Button, Checkbox, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrChangeType, DcrImplementBody } from "../../lib/types";
import { useImplementDcr } from "./mutations";

// Conditionally mounted by DcrAdvancePanel (state===Approved, change_type !== CREATE). REVISE = a plain
// confirm (the backend resolves the target's latest Approved version). RETIRE = a confirm that, on a
// 409 obsoletion_blocked, reveals the coverage-gap reason + a force-retire escalation (a required
// justification). CREATE-implement is deferred (no SPA version_id→document_id resolution).
export function ImplementDcrModal({
  dcrId,
  changeType,
  onClose,
}: {
  dcrId: string;
  changeType: DcrChangeType;
  onClose: () => void;
}) {
  const m = useImplementDcr(dcrId);
  const isRetire = changeType === "RETIRE";
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false); // a 409 obsoletion_blocked surfaced → show escalation
  const [forceRetire, setForceRetire] = useState(false);
  const [justification, setJustification] = useState("");

  // Force-retire requires a non-empty justification (the server 422s an empty one).
  const submitDisabled =
    m.isPending || (blocked && forceRetire && justification.trim().length === 0);

  async function submit() {
    setError(null);
    const body: DcrImplementBody = isRetire
      ? {
          force_retire: forceRetire,
          ...(forceRetire ? { override_justification: justification.trim() } : {}),
        }
      : {};
    try {
      await m.mutateAsync(body);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "obsoletion_blocked") {
        // Reveal the coverage-gap reason + the force-retire escalation (don't close).
        setBlocked(true);
        setError(e.message);
        return;
      }
      // Other 409s (no_approved_draft / version_already_linked / version_not_approved / dcr_not_implementable)
      // + 403 sod_violation → the server's word; the onSettled invalidate refreshes the drawer.
      setError(e instanceof ApiError ? e.message : "Could not implement the change request.");
    }
  }

  const title = isRetire ? "Retire document" : "Implement change";
  const action =
    blocked && forceRetire ? "Force-retire" : isRetire ? "Retire document" : "Implement";

  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <Stack gap="sm">
        {error && (
          <Alert color={blocked ? "yellow" : "red"} title={blocked ? "Coverage gap" : undefined}>
            {error}
          </Alert>
        )}
        {!blocked && (
          <Text size="sm">
            {isRetire
              ? "This obsoletes the target document once the change takes effect. The vault checks for a coverage gap first."
              : "This schedules the approved revision to take effect (the cutover sweep promotes it). You can close the change request once it is Effective."}
          </Text>
        )}
        {blocked && (
          <Stack gap="xs">
            <Checkbox
              checked={forceRetire}
              onChange={(e) => setForceRetire(e.currentTarget.checked)}
              label="Force-retire anyway (records an override justification)"
            />
            {forceRetire && (
              <Textarea
                label="Justification"
                required
                autosize
                minRows={2}
                value={justification}
                onChange={(e) => setJustification(e.currentTarget.value)}
              />
            )}
          </Stack>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Not yet
          </Button>
          <Button
            color={blocked && forceRetire ? "red" : undefined}
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={submitDisabled}
          >
            {action}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
