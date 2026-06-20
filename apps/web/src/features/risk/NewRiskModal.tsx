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
import type { RiskCreateBody, RiskType } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { useProcesses } from "../objectives/hooks";
import { bandForCell, cellRating, MATRIX_AXIS } from "./matrix";
import { RISK_BAND_LABEL, RISK_BAND_TONE } from "./labels";
import { useCreateRisk } from "./mutations";

const SCALE = MATRIX_AXIS.map((n) => String(n));

// Create a risk row. requireProcess (a PROCESS-only `register.manage` holder) makes the process picker
// required — a process-less submit would 403 at the server's SYSTEM-scope enforce, so we gate the
// button on a pick rather than let it fail (the RaiseCapaModal idiom). Conditionally mounted by the
// page so close discards the draft. No clause picker in v1 (a named residual — clause_id is an optional
// per-risk tag the backend accepts but the FE doesn't surface yet).
export function NewRiskModal({
  opened,
  onClose,
  onCreated,
  requireProcess = false,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  requireProcess?: boolean;
}) {
  const m = useCreateRisk();
  const { data: processes } = useProcesses();
  const [type, setType] = useState<RiskType>("risk");
  const [description, setDescription] = useState("");
  const [likelihood, setLikelihood] = useState<string | null>(null);
  const [severity, setSeverity] = useState<string | null>(null);
  const [treatment, setTreatment] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const l = likelihood ? Number(likelihood) : null;
  const s = severity ? Number(severity) : null;
  const previewBand = l !== null && s !== null ? bandForCell(l, s) : null;

  const canSubmit =
    description.trim() !== "" && l !== null && s !== null && (!requireProcess || !!processId);

  async function submit() {
    setError(null);
    if (l === null || s === null) return;
    try {
      const created = await m.mutateAsync({
        type,
        description: description.trim(),
        likelihood: l,
        severity: s,
        process_id: processId ?? undefined,
        treatment: treatment.trim() || undefined,
      } satisfies RiskCreateBody);
      onCreated(created.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the risk.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New risk or opportunity">
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
            required
            placeholder="1–5"
            value={likelihood}
            onChange={setLikelihood}
            data={SCALE}
            comboboxProps={{ keepMounted: false }}
          />
          <Select
            label="Severity"
            required
            placeholder="1–5"
            value={severity}
            onChange={setSeverity}
            data={SCALE}
            comboboxProps={{ keepMounted: false }}
          />
        </Group>
        {previewBand && l !== null && s !== null && (
          <Group gap="xs">
            <StatusBadge
              tone={RISK_BAND_TONE[previewBand]}
              label={`${RISK_BAND_LABEL[previewBand]} · rating ${cellRating(l, s)}`}
              kind="Band"
            />
          </Group>
        )}
        {processes && processes.length > 0 && (
          <Select
            label={requireProcess ? "Process" : "Process (optional)"}
            required={requireProcess}
            clearable={!requireProcess}
            placeholder={requireProcess ? "Pick the owning process" : undefined}
            value={processId}
            onChange={setProcessId}
            data={processes.map((p) => ({ value: p.id, label: p.name }))}
            comboboxProps={{ keepMounted: false }}
          />
        )}
        <Textarea
          label="Treatment (optional)"
          value={treatment}
          onChange={(e) => setTreatment(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!canSubmit}>
            Create risk
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
