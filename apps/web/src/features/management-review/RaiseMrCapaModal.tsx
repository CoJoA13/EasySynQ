import { Alert, Button, Group, Modal, Select, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcSeverity } from "../../lib/types";
import { useRaiseMrCapa } from "./mutations";

export function RaiseMrCapaModal({
  opened,
  reviewId,
  outputId,
  onClose,
  onCreated,
}: {
  opened: boolean;
  reviewId: string;
  outputId: string;
  onClose: () => void;
  onCreated: (capaId: string) => void;
}) {
  const m = useRaiseMrCapa();
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!severity) return;
    try {
      const ro = await m.mutateAsync({ id: reviewId, oid: outputId, severity });
      if (ro.spawned_capa_id) onCreated(ro.spawned_capa_id);
      setSeverity(null);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the CAPA.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Raise CAPA from this action">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm" c="dimmed">
          Spawns a corrective/preventive action tracked in the CAPA system. Pick its severity.
        </Text>
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!severity}>
            Raise CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
