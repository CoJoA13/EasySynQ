// apps/web/src/features/capa/RaiseCapaModal.tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { CapaRaiseBody, CapaSource, NcSeverity } from "../../lib/types";
import { useProcesses } from "../objectives/hooks";
import { useRaiseCapa } from "./mutations";

// source omits review_output (reserved for the Management-Review family — the API 422s it).
const SOURCES: { value: CapaSource; label: string }[] = [
  { value: "audit", label: "Audit" },
  { value: "process", label: "Process" },
  { value: "complaint", label: "Complaint" },
];

export function RaiseCapaModal({
  opened,
  onClose,
  onCreated,
  requireProcess = false,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
  // True when the caller can raise ONLY at PROCESS scope (no SYSTEM capa.create). The picker then
  // becomes required — a process-less submit would 403 at the server's SYSTEM-scope enforce, so we
  // gate the button on a pick rather than let it fail. A SYSTEM-create holder leaves it optional.
  requireProcess?: boolean;
}) {
  const m = useRaiseCapa();
  // Process scope: a bound Process-Owner holds capa.create only at their owned process(es), so the
  // raise must carry that process_id for the server's PROCESS-scoped enforce to pass. Omit the picker
  // (and stay byte-identical to the SYSTEM/ad-hoc raise) when the caller can't read any process.
  const { data: processes } = useProcesses();
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [source, setSource] = useState<CapaSource>("process");
  const [problem, setProblem] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!severity) return;
    try {
      const capa = await m.mutateAsync({
        title,
        severity,
        source,
        problem: problem.trim() || undefined,
        process_id: processId ?? undefined,
      } satisfies CapaRaiseBody);
      onCreated(capa.id);
      setTitle("");
      setSeverity(null);
      setProblem("");
      setProcessId(null);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the CAPA.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Raise CAPA">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput
          label="Title"
          required
          value={title}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Select
          label="Source"
          value={source}
          onChange={(v) => setSource((v as CapaSource) ?? "process")}
          data={SOURCES}
          comboboxProps={{ keepMounted: false }}
        />
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
          label="Problem (optional)"
          value={problem}
          onChange={(e) => setProblem(e.currentTarget.value)}
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
            disabled={title.trim().length === 0 || !severity || (requireProcess && !processId)}
          >
            Raise CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
