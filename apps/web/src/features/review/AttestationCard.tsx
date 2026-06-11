import { Alert, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useAcknowledgeTask } from "./ackHooks";

const CODE_COPY: Record<string, string> = {
  ack_obligation_lapsed: "This document no longer requires your acknowledgement — it may be under revision or obsoleted.",
  ack_superseded: "A newer major revision was released — acknowledge the current version instead.",
  conflict: "You've already acknowledged this.",
};

// S-ack-2: the DOC_ACK attestation. Acknowledge-only, NO signature (R43 — an ack is append-only
// evidence, never a signature_event), so this is NOT a DecisionCard: prominent copy + one button.
export function AttestationCard({ taskId, documentId }: { taskId: string; documentId: string }) {
  const ack = useAcknowledgeTask();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // best-effort label for the copy (the obligation stands regardless of read).
  const { data: doc } = useDocument(documentId, { enabled: true, retry: false });
  const label = doc ? `${doc.identifier}${doc.current_effective_version_id ? "" : ""}` : "this document";

  async function submit() {
    setError(null);
    try {
      await ack.mutateAsync({ taskId, documentId });
      navigate("/tasks");
    } catch (e) {
      if (e instanceof ApiError) setError(CODE_COPY[e.code] ?? e.message);
      else setError("Something went wrong. Please retry.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>I have read &amp; understood</Text>
        <Text size="sm">
          By acknowledging, you confirm you have read and understood <b>{label}</b>.
        </Text>
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>Cancel</Button>
          <Button onClick={() => void submit()} loading={ack.isPending}>I have read &amp; understood</Button>
        </Group>
      </Stack>
    </Card>
  );
}
