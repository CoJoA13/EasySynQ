import { Alert, Button, Group, Modal, Select, Stack, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcrSource, NcSeverity } from "../../lib/types";
import { NCR_SOURCE_LABEL, NCR_SOURCES } from "./intake";
import { useCreateNcr } from "./mutations";

export function NcrForm({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const m = useCreateNcr();
  const [source, setSource] = useState<NcrSource | null>(null);
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setSource(null);
    setSeverity(null);
    setDescription("");
    setError(null);
  }
  async function submit() {
    setError(null);
    if (!source || !severity) return;
    try {
      await m.mutateAsync({ source, severity, description });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the NCR.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="Raise an NCR">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Select
          label="Source"
          required
          placeholder="Pick a source"
          value={source}
          onChange={(v) => setSource(v as NcrSource | null)}
          data={NCR_SOURCES.map((s) => ({ value: s, label: NCR_SOURCE_LABEL[s] }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Textarea
          label="Description"
          required
          autosize
          minRows={3}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!source || !severity || description.trim().length === 0}
          >
            Raise NCR
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
