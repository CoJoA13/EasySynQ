import { Alert, Button, Group, Modal, NumberInput, Stack, Switch, Text } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

// S-web-8: edit the D5 review cadence (PATCH /documents/{id}, document.manage_metadata). Clearing
// sends an EXPLICIT null — the PATCH consumes model_fields_set, so an omitted key inherits. The
// response is NOT consumed (its effective_from is null on write paths) — invalidate + refetch.
// Parents must render this conditionally ({open && <ReviewPeriodModal …>}) so close unmounts it.
export function ReviewPeriodModal({
  doc,
  opened,
  onClose,
}: {
  doc: DocumentSummary;
  opened: boolean;
  onClose: () => void;
}) {
  const api = useApi();
  const qc = useQueryClient();
  const [months, setMonths] = useState<number | string>(doc.review_period_months ?? 24);
  const [clear, setClear] = useState(doc.review_period_months === null);
  const [error, setError] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: (review_period_months: number | null) =>
      api.send<DocumentSummary>("PATCH", `/api/v1/documents/${doc.id}`, { review_period_months }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["document", doc.id] });
      onClose();
    },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please retry."),
  });

  const n = typeof months === "number" ? months : Number(months);
  const invalid = !clear && (!Number.isInteger(n) || n < 1 || n > 120);

  return (
    <Modal opened={opened} onClose={onClose} title={`Review period — ${doc.identifier}`}>
      <Stack gap="sm">
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Switch
          checked={clear}
          onChange={(e) => setClear(e.currentTarget.checked)}
          label="No scheduled review"
        />
        {!clear && (
          <NumberInput
            label="Review period (months)"
            min={1}
            max={120}
            value={months}
            onChange={setMonths}
            clampBehavior="none"
          />
        )}
        <Text size="xs" c="dimmed">
          The next review date is recomputed by the server — anchored on the later of the last
          review and the effective date.
        </Text>
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => update.mutate(clear ? null : n)}
            loading={update.isPending}
            disabled={invalid}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
