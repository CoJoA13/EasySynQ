import {
  Alert, Button, Checkbox, Group, Input, Modal, SegmentedControl, Stack, Text, TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Objective, ObjectiveDirection, ObjectiveUpdateBody } from "../../lib/types";
import { useUpdateObjective } from "./mutations";
import { useEffectivePolicy } from "./hooks";
import { BandPreview } from "./BandPreview";

interface Props {
  opened: boolean;
  objective: Objective;
  onClose: () => void;
}

// S-obj-4 (O-1): edit the working-copy commitment. Seeds from pending_commitment when a revision
// edit is already in flight (the detail's MAIN fields are the GOVERNING values — seeding from
// them would silently revert a prior edit), else from the objective fields. Sends the full body
// (explicit null clears); policy_id only when a current POL loaded (omit-inherits otherwise).
// Parent renders {open && <EditCommitmentModal/>} so close
// unmounts + resets (the S-web-7d persistently-mounted-modal trap).
export function EditCommitmentModal({ opened, objective, onClose }: Props) {
  const update = useUpdateObjective(objective.id);
  const { data: policy, isError: policyError, isLoading: policyLoading } = useEffectivePolicy();
  const [error, setError] = useState<string | null>(null);

  // Seed from pending_commitment when present — the main fields are the GOVERNING values once a
  // governing version exists; seeding from them when a pending edit exists would silently revert.
  const seed = objective.pending_commitment != null
    ? objective.pending_commitment
    : {
        target_value: objective.target_value,
        unit: objective.unit,
        direction: objective.direction,
        due_date: objective.due_date,
        at_risk_threshold: objective.at_risk_threshold,
        baseline_value: objective.baseline_value,
        policy_id: objective.policy_id,
      };

  const [target, setTarget] = useState(seed.target_value);
  const [unit, setUnit] = useState(seed.unit);
  const [direction, setDirection] = useState<ObjectiveDirection>(seed.direction);
  const [dueDate, setDueDate] = useState(seed.due_date);
  const [baseline, setBaseline] = useState(seed.baseline_value ?? "");
  const [threshold, setThreshold] = useState(seed.at_risk_threshold ?? "");
  const [linkPolicy, setLinkPolicy] = useState(seed.policy_id != null);

  const targetIsNumber = target.trim() !== "" && !Number.isNaN(Number(target));
  const canSave = targetIsNumber && unit.trim() !== "" && dueDate !== "";

  async function save() {
    setError(null);
    const body: ObjectiveUpdateBody = {
      target_value: target.trim(),
      unit: unit.trim(),
      direction,
      due_date: dueDate,
      at_risk_threshold: threshold.trim() === "" ? null : threshold.trim(),
      baseline_value: baseline.trim() === "" ? null : baseline.trim(),
    };
    // The policy link is managed ONLY when a current Effective Policy loaded (the checkbox is
    // the sole affordance). On an errored/loading read OR a successfully-null one, OMIT the key
    // — the API inherits the working value (sending null would silently unlink a seeded policy;
    // sending a lapsed seed back would 422 against the Effective-POL check) — Codex P2.
    if (!policyError && !policyLoading && policy) {
      body.policy_id = linkPolicy ? policy.id : null;
    }
    try {
      await update.mutateAsync(body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong saving the commitment.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Edit commitment">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Group grow>
          <TextInput
            label="Target"
            required
            value={target}
            onChange={(e) => setTarget(e.currentTarget.value)}
          />
          <TextInput
            label="Unit"
            required
            value={unit}
            onChange={(e) => setUnit(e.currentTarget.value)}
          />
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
          type="date"
          label="Due date"
          required
          value={dueDate}
          onChange={(e) => setDueDate(e.currentTarget.value)}
        />
        <Group grow>
          <TextInput
            label="Baseline"
            value={baseline}
            onChange={(e) => setBaseline(e.currentTarget.value)}
          />
          <TextInput
            label="At-risk threshold"
            value={threshold}
            onChange={(e) => setThreshold(e.currentTarget.value)}
          />
        </Group>
        <BandPreview target={target} threshold={threshold} direction={direction} />
        {policy ? (
          <Checkbox
            label={`Consistent with ${policy.identifier} — ${policy.title}`}
            checked={linkPolicy}
            onChange={(e) => setLinkPolicy(e.currentTarget.checked)}
          />
        ) : policyError ? (
          // Neutral copy on an errored read — never the positive "no policy yet" (S-home-1 class).
          <Text size="xs" c="dimmed">
            Couldn&apos;t load the Quality Policy — you can still save.
          </Text>
        ) : policyLoading ? null : (
          <Text size="xs" c="dimmed">
            No effective Quality Policy yet — the link is optional.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => void save()}
            loading={update.isPending}
            disabled={!canSave}
          >
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
