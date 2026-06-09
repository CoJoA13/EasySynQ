import { Alert, Button, Group, Modal, Select, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Complaint, NcSeverity } from "../../lib/types";
import { useSpawnCapa } from "./mutations";

// Spawn a CAPA from a complaint. A CAPA REQUIRES a severity (the backend 422s without one — and it
// prefers the request severity as "late triage", service.py:227), so we confirm severity here rather
// than silently inheriting: the Select pre-fills the complaint's severity when it has one, and forces a
// pick when it doesn't (a complaint can be logged severity-less). On success the complaint list
// invalidates and the row flips Spawn→View.
export function SpawnCapaModal({
  complaint,
  opened,
  onClose,
}: {
  complaint: Complaint;
  opened: boolean;
  onClose: () => void;
}) {
  const m = useSpawnCapa();
  const [severity, setSeverity] = useState<NcSeverity | null>(complaint.severity);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!severity) return;
    try {
      await m.mutateAsync({ complaintId: complaint.id, severity });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not spawn a CAPA.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="Spawn a CAPA">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm" c="dimmed">
          Raise a corrective/preventive action from {complaint.identifier ?? "this complaint"}. Confirm
          its severity.
        </Text>
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!severity}>
            Spawn CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
