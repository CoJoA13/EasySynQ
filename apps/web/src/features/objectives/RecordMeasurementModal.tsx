import { Alert, Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { MeasurementCreateBody } from "../../lib/types";
import { useRecordMeasurement } from "./mutations";

interface Props {
  opened: boolean;
  objectiveId: string;
  unit: string;
  onClose: () => void;
  onRecorded: () => void;
}

export function RecordMeasurementModal({ opened, objectiveId, unit, onClose, onRecorded }: Props) {
  const record = useRecordMeasurement(objectiveId);
  const [period, setPeriod] = useState("");
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);

  const valueIsNumber = value.trim() !== "" && !Number.isNaN(Number(value));
  const canSubmit = period !== "" && valueIsNumber;

  async function submit() {
    setError(null);
    const body: MeasurementCreateBody = {
      period,
      value: value.trim(),
      unit, // LOCKED to the objective's unit — can never diverge → never trips the 422
      source: source.trim() === "" ? null : source.trim(),
    };
    try {
      await record.mutateAsync(body);
      onRecorded();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong recording the measurement.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Record measurement">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput type="date" label="Period" required value={period} onChange={(e) => setPeriod(e.currentTarget.value)} />
        <TextInput
          label="Value" required value={value} onChange={(e) => setValue(e.currentTarget.value)}
          rightSection={<span style={{ paddingRight: 8, color: "var(--mantine-color-dimmed)" }}>{unit}</span>}
          rightSectionWidth={Math.max(28, unit.length * 9 + 16)}
        />
        <TextInput label="Source (optional)" value={source} onChange={(e) => setSource(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={record.isPending} disabled={!canSubmit}>
            Record
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
