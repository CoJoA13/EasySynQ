import { Alert, Button, Group, Modal, SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ChangeSignificance, DcrDetail, DcrPatchBody, DcrReasonClass } from "../../lib/types";
import { proposedEffectiveIso } from "./DcrRaiseFields";
import { REASON_LABEL } from "./labels";
import { usePatchDcr } from "./mutations";

// Conditionally mounted by DcrAdvancePanel; seeded from the current dcr. Open-only at the call site.
export function EditDcrModal({ dcr, onClose }: { dcr: DcrDetail; onClose: () => void }) {
  const m = usePatchDcr(dcr.id);
  const [reasonText, setReasonText] = useState(dcr.reason_text);
  const [reasonClass, setReasonClass] = useState<DcrReasonClass>(dcr.reason_class);
  const [significance, setSignificance] = useState<ChangeSignificance>(dcr.change_significance);
  const [effectiveFrom, setEffectiveFrom] = useState(
    dcr.proposed_effective_from ? dcr.proposed_effective_from.slice(0, 10) : "",
  );
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (reasonText.trim().length === 0) return;
    const body: DcrPatchBody = {
      reason_text: reasonText.trim(),
      reason_class: reasonClass,
      change_significance: significance,
      proposed_effective_from: proposedEffectiveIso(effectiveFrom || null),
    };
    try {
      await m.mutateAsync(body);
      onClose();
    } catch (e) {
      // 409 dcr_not_editable (concurrent advance) — surface the server word; the onSettled invalidate
      // refreshes the drawer to the real state behind this calm error.
      setError(e instanceof ApiError ? e.message : "Could not save the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Edit change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea
          label="Reason for change"
          required
          autosize
          minRows={2}
          value={reasonText}
          onChange={(e) => setReasonText(e.currentTarget.value)}
        />
        <Select
          label="Reason class"
          required
          value={reasonClass}
          onChange={(v) => v && setReasonClass(v as DcrReasonClass)}
          data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(([value, label]) => ({
            value,
            label,
          }))}
          comboboxProps={{ keepMounted: false }}
        />
        <div>
          <Text size="sm" fw={500} mb={4}>
            Significance
          </Text>
          <SegmentedControl
            value={significance}
            onChange={(v) => setSignificance(v as ChangeSignificance)}
            data={[
              { value: "MINOR", label: "Minor" },
              { value: "MAJOR", label: "Major" },
            ]}
          />
        </div>
        <TextInput
          type="date"
          label="Proposed effective from (optional)"
          value={effectiveFrom}
          onChange={(e) => setEffectiveFrom(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          {/* "Discard" not "Cancel" — the DCR's own Cancel (withdraw) action is a sibling button in the
              drawer's DcrAdvancePanel, so a "Cancel" here would read as withdrawing the change request. */}
          <Button variant="subtle" onClick={onClose}>
            Discard
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={reasonText.trim().length === 0}>
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
