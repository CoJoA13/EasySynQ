import {
  Alert, Button, Checkbox, Collapse, Group, Input, Modal, SegmentedControl, Select, Stack, Text,
  TextInput, UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ObjectiveCreateBody, ObjectiveDirection } from "../../lib/types";
import { useCreateObjective } from "./mutations";
import { useEffectivePolicy, useProcesses } from "./hooks";
import { BandPreview } from "./BandPreview";

interface Props {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}

export function NewObjectiveModal({ opened, onClose, onCreated }: Props) {
  const create = useCreateObjective();
  const { data: processes } = useProcesses();
  const { data: policy } = useEffectivePolicy();
  const [advanced, advancedC] = useDisclosure(false);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("");
  const [unit, setUnit] = useState("");
  const [direction, setDirection] = useState<ObjectiveDirection>("HIGHER_IS_BETTER");
  const [dueDate, setDueDate] = useState("");
  const [baseline, setBaseline] = useState("");
  const [threshold, setThreshold] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);
  const [linkPolicy, setLinkPolicy] = useState(false);

  const targetIsNumber = target.trim() !== "" && !Number.isNaN(Number(target));
  const canSubmit = title.trim() !== "" && targetIsNumber && unit.trim() !== "" && dueDate !== "";

  async function submit() {
    setError(null);
    const body: ObjectiveCreateBody = {
      title: title.trim(),
      target_value: target.trim(),
      unit: unit.trim(),
      direction,
      due_date: dueDate,
      baseline_value: baseline.trim() === "" ? null : baseline.trim(),
      at_risk_threshold: threshold.trim() === "" ? null : threshold.trim(),
      process_id: processId,
      policy_id: linkPolicy && policy ? policy.id : null,
    };
    try {
      const created = await create.mutateAsync(body);
      onCreated(created.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong creating the objective.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New quality objective">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput label="Objective" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <Group grow>
          <TextInput label="Target" required value={target} onChange={(e) => setTarget(e.currentTarget.value)} />
          <TextInput label="Unit" required value={unit} onChange={(e) => setUnit(e.currentTarget.value)} />
        </Group>
        <Input.Wrapper label="Direction">
          <SegmentedControl
            fullWidth
            value={direction}
            onChange={(v) => setDirection(v as ObjectiveDirection)}
            data={[
              { value: "HIGHER_IS_BETTER", label: "Higher is better" },
              { value: "LOWER_IS_BETTER", label: "Lower is better" },
            ]}
          />
        </Input.Wrapper>
        <TextInput
          type="date" label="Due date" required value={dueDate}
          onChange={(e) => setDueDate(e.currentTarget.value)}
        />
        <UnstyledButton onClick={advancedC.toggle} c="dimmed" fz="sm">
          {advanced ? "▾" : "▸"} Amber &quot;at-risk&quot; band &amp; baseline (optional)
        </UnstyledButton>
        <Collapse in={advanced}>
          <Stack gap="sm">
            <Group grow>
              <TextInput label="Baseline" value={baseline} onChange={(e) => setBaseline(e.currentTarget.value)} />
              <TextInput label="At-risk threshold" value={threshold} onChange={(e) => setThreshold(e.currentTarget.value)} />
            </Group>
            <BandPreview target={target} threshold={threshold} direction={direction} />
            {processes && processes.length > 0 && (
              <Select
                label="Process (optional)"
                clearable
                value={processId}
                onChange={setProcessId}
                data={processes.map((p) => ({ value: p.id, label: p.name }))}
                comboboxProps={{ keepMounted: false }}
              />
            )}
            {policy ? (
              <Checkbox
                label={`Consistent with ${policy.identifier} — ${policy.title}`}
                checked={linkPolicy}
                onChange={(e) => setLinkPolicy(e.currentTarget.checked)}
              />
            ) : (
              <Text size="xs" c="dimmed">
                No effective Quality Policy yet — you can link one later.
              </Text>
            )}
          </Stack>
        </Collapse>
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={create.isPending} disabled={!canSubmit}>
            Create objective
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
