import { Alert, Button, Group, Modal, Select, Stack, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Ncr, NcrDisposition } from "../../lib/types";
import { DISPOSITION_LABEL, DISPOSITIONS } from "./intake";
import { useNcrDisposition } from "./mutations";

export function DispositionModal({
  ncr,
  opened,
  onClose,
}: {
  ncr: Ncr;
  opened: boolean;
  onClose: () => void;
}) {
  const m = useNcrDisposition(ncr.id);
  const [disposition, setDisposition] = useState<NcrDisposition | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!disposition) return;
    try {
      await m.mutateAsync({ disposition, notes: notes.trim() || undefined });
      setDisposition(null);
      setNotes("");
      onClose();
    } catch (e) {
      // 409 ncr_already_dispositioned (a race) lands here — surface the server message calmly.
      setError(e instanceof ApiError ? e.message : "Could not record the disposition.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title={`Record disposition — ${ncr.identifier}`}>
      <Stack gap="sm">
        {error && <Alert color="orange">{error}</Alert>}
        <Select
          label="Disposition (ISO 9001 §8.7)"
          required
          placeholder="Pick a disposition"
          value={disposition}
          onChange={(v) => setDisposition(v as NcrDisposition | null)}
          data={DISPOSITIONS.map((d) => ({ value: d, label: DISPOSITION_LABEL[d] }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Textarea
          label="Notes"
          autosize
          minRows={2}
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!disposition}>
            Record disposition
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
