import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcSeverity } from "../../lib/types";
import { useCreateComplaint } from "./mutations";

export function ComplaintForm({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const m = useCreateComplaint();
  const [description, setDescription] = useState("");
  const [customer, setCustomer] = useState("");
  const [channel, setChannel] = useState("");
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setDescription("");
    setCustomer("");
    setChannel("");
    setSeverity(null);
    setError(null);
  }
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({
        description,
        customer: customer.trim() || undefined,
        channel: channel.trim() || undefined,
        severity: severity ?? undefined,
      });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not log the complaint.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="Log a complaint">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea
          label="Description"
          required
          autosize
          minRows={3}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <TextInput label="Customer" value={customer} onChange={(e) => setCustomer(e.currentTarget.value)} />
        <TextInput
          label="Channel"
          placeholder="email, phone, portal…"
          value={channel}
          onChange={(e) => setChannel(e.currentTarget.value)}
        />
        <Select
          label="Severity"
          placeholder="Optional"
          clearable
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={description.trim().length === 0}>
            Log complaint
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
