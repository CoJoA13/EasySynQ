import {
  Alert,
  Button,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Textarea,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { RiskRow, RiskType, RiskUpdateBody } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { useProcesses } from "../objectives/hooks";
import { bandForCell, cellRating, MATRIX_AXIS } from "./matrix";
import { RISK_BAND_LABEL, RISK_BAND_TONE } from "./labels";
import { useUpdateRisk } from "./mutations";

const SCALE = MATRIX_AXIS.map((n) => String(n));

// Edit a risk row — a partial PATCH sending ONLY changed fields (the backend's exclude_unset; omitted
// ≠ null). likelihood/severity changes re-derive risk_rating server-side; treatment/effectiveness/
// process clear to null when emptied. scoring_method is write-once (not editable). The drawer gates
// mounting on register.manage @ the row's process AND the head being editable; conditionally mounted.
export function EditRiskModal({
  opened,
  onClose,
  risk,
}: {
  opened: boolean;
  onClose: () => void;
  risk: RiskRow;
}) {
  const m = useUpdateRisk(risk.id);
  const { data: processes } = useProcesses();
  const [type, setType] = useState<RiskType>(risk.type);
  const [description, setDescription] = useState(risk.description);
  const [likelihood, setLikelihood] = useState(String(risk.likelihood));
  const [severity, setSeverity] = useState(String(risk.severity));
  const [treatment, setTreatment] = useState(risk.treatment ?? "");
  const [effectiveness, setEffectiveness] = useState(risk.effectiveness ?? "");
  const [processId, setProcessId] = useState<string | null>(risk.process_id);
  const [error, setError] = useState<string | null>(null);

  const l = Number(likelihood);
  const s = Number(severity);
  const previewBand = bandForCell(l, s);

  function buildPatch(): RiskUpdateBody {
    const patch: RiskUpdateBody = {};
    if (type !== risk.type) patch.type = type;
    if (description.trim() && description.trim() !== risk.description)
      patch.description = description.trim();
    if (l !== risk.likelihood) patch.likelihood = l;
    if (s !== risk.severity) patch.severity = s;
    const t = treatment.trim() || null;
    if (t !== (risk.treatment || null)) patch.treatment = t;
    const eff = effectiveness.trim() || null;
    if (eff !== (risk.effectiveness || null)) patch.effectiveness = eff;
    if (processId !== risk.process_id) patch.process_id = processId;
    return patch;
  }

  const patch = buildPatch();
  const dirty = Object.keys(patch).length > 0;

  async function submit() {
    setError(null);
    if (!dirty) {
      onClose();
      return;
    }
    try {
      await m.mutateAsync(patch);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the risk.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Edit risk or opportunity">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <SegmentedControl
          aria-label="Type"
          value={type}
          onChange={(v) => setType(v as RiskType)}
          data={[
            { value: "risk", label: "Risk" },
            { value: "opportunity", label: "Opportunity" },
          ]}
        />
        <Textarea
          label="Description"
          required
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group grow>
          <Select
            label="Likelihood"
            value={likelihood}
            onChange={(v) => setLikelihood(v ?? likelihood)}
            data={SCALE}
            comboboxProps={{ keepMounted: false }}
          />
          <Select
            label="Severity"
            value={severity}
            onChange={(v) => setSeverity(v ?? severity)}
            data={SCALE}
            comboboxProps={{ keepMounted: false }}
          />
        </Group>
        <Group gap="xs">
          <StatusBadge
            tone={RISK_BAND_TONE[previewBand]}
            label={`${RISK_BAND_LABEL[previewBand]} · rating ${cellRating(l, s)}`}
            kind="Band"
          />
        </Group>
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
        <Textarea
          label="Treatment"
          value={treatment}
          onChange={(e) => setTreatment(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Textarea
          label="Effectiveness"
          value={effectiveness}
          onChange={(e) => setEffectiveness(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!dirty || description.trim() === ""}
          >
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
