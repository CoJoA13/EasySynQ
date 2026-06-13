import { SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useDocuments } from "../library/useDocuments";
import type { ChangeSignificance, DcrChangeType } from "../../lib/types";
import { CHANGE_TYPE_LABEL } from "./labels";

export interface DcrFieldsValue {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_text: string;
  target_document_id: string | null;
  proposed_effective_from: string | null; // YYYY-MM-DD (native date input) | null
}

export const EMPTY_DCR_FIELDS: DcrFieldsValue = {
  change_type: "REVISE",
  change_significance: "MINOR",
  reason_text: "",
  target_document_id: null,
  proposed_effective_from: null,
};

// reason non-empty AND (CREATE has no target | REVISE/RETIRE has its target) — mirrors the backend
// CREATE⟺no-target biconditional so create_has_target/target_required are unreachable from the UI.
export function isDcrFieldsValid(v: DcrFieldsValue): boolean {
  return v.reason_text.trim().length > 0 && (v.change_type === "CREATE" || v.target_document_id !== null);
}

// A native date (YYYY-MM-DD) → the UTC-midnight ISO-8601 instant of that calendar date (R8:
// proposed_effective_from is a timestamptz the release sweep schedules off); null when unset.
export function proposedEffectiveIso(date: string | null): string | null {
  return date ? `${date}T00:00:00+00:00` : null;
}

export function DcrRaiseFields({
  value,
  onChange,
}: {
  value: DcrFieldsValue;
  onChange: (v: DcrFieldsValue) => void;
}) {
  // The target lists Effective controlled Documents (the revise/retire target). useDocuments has no
  // free-text filter, so the Select is `searchable` (client-side label filter) over a generous page.
  const { data: docsPage } = useDocuments({ current_state: "Effective" }, { limit: 200, offset: 0 });
  const targetOptions = (docsPage?.data ?? [])
    .filter((d) => d.kind === "DOCUMENT")
    .map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` }));
  const showTarget = value.change_type !== "CREATE";

  return (
    <Stack gap="sm">
      <div>
        <Text size="sm" fw={500} mb={4}>
          Change type
        </Text>
        <SegmentedControl
          fullWidth
          value={value.change_type}
          onChange={(v) =>
            onChange({
              ...value,
              change_type: v as DcrChangeType,
              // switching to CREATE clears the target so the body never carries a CREATE-with-target
              target_document_id: v === "CREATE" ? null : value.target_document_id,
            })
          }
          data={(Object.entries(CHANGE_TYPE_LABEL) as [DcrChangeType, string][]).map(([val, label]) => ({
            value: val,
            label,
          }))}
        />
      </div>

      {showTarget && (
        <Select
          label="Target document"
          required
          searchable
          placeholder="Pick the document to revise or retire"
          value={value.target_document_id}
          onChange={(v) => onChange({ ...value, target_document_id: v })}
          data={targetOptions}
          nothingFoundMessage="No matching documents"
          comboboxProps={{ keepMounted: false }}
        />
      )}

      <div>
        <Text size="sm" fw={500} mb={4}>
          Significance
        </Text>
        <SegmentedControl
          value={value.change_significance}
          onChange={(v) => onChange({ ...value, change_significance: v as ChangeSignificance })}
          data={[
            { value: "MINOR", label: "Minor" },
            { value: "MAJOR", label: "Major" },
          ]}
        />
      </div>

      <Textarea
        label="Reason for change"
        required
        autosize
        minRows={2}
        value={value.reason_text}
        onChange={(e) => onChange({ ...value, reason_text: e.currentTarget.value })}
      />

      <TextInput
        type="date"
        label="Proposed effective from (optional)"
        value={value.proposed_effective_from ?? ""}
        onChange={(e) => onChange({ ...value, proposed_effective_from: e.currentTarget.value || null })}
      />
    </Stack>
  );
}
