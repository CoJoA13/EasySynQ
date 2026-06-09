// apps/web/src/features/capa/RaiseCapaModal.tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { CapaSource, NcSeverity } from "../../lib/types";
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
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useRaiseCapa();
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [source, setSource] = useState<CapaSource>("process");
  const [problem, setProblem] = useState("");
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
      });
      onCreated(capa.id);
      setTitle("");
      setSeverity(null);
      setProblem("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the CAPA.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Raise CAPA">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput label="Title" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
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
            disabled={title.trim().length === 0 || !severity}
          >
            Raise CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
