import { Loader, SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useMemo, useState } from "react";
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
  return (
    v.reason_text.trim().length > 0 && (v.change_type === "CREATE" || v.target_document_id !== null)
  );
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
  // The target lists Effective controlled Documents (the revise/retire target). A debounced `q`
  // narrows server-side (GET /documents — substring ILIKE on identifier/title), so a target beyond
  // the old 200-row client cap is reachable. react-query keys by `q`, so a slow earlier response
  // can't clobber a newer one (the stale-response race the CommandPalette guards by hand is a
  // non-issue here). document.read is a filter-not-403 → a low-scope caller sees fewer rows, never a
  // crash. The server already excludes Records (kind=DOCUMENT), so no client kind filter is needed.
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 200);
  const {
    data: docsPage,
    isFetching,
    isError,
  } = useDocuments(
    { current_state: "Effective", q: debounced.trim() || undefined },
    { limit: 20, offset: 0 },
  );
  // Remember the picked option's label so the Select can always resolve the COMMITTED target's
  // label even after a later, narrower search drops it from the page (the Codex stale-row edge).
  const [selected, setSelected] = useState<{ value: string; label: string } | null>(null);
  const serverOptions = useMemo(
    () =>
      (docsPage?.data ?? []).map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` })),
    [docsPage],
  );
  const targetOptions = useMemo(() => {
    // Union the remembered option in ONLY while it is the committed target and the current page
    // doesn't already carry it. Gating on target_document_id makes a stale pick inert the moment
    // the parent clears the target (the CREATE switch), so no orphan option can leak — no effect
    // needed, and the picker re-shows clean for REVISE (the Select unmounts on CREATE + remounts).
    if (
      !selected ||
      selected.value !== value.target_document_id ||
      serverOptions.some((o) => o.value === selected.value)
    ) {
      return serverOptions;
    }
    return [selected, ...serverOptions];
  }, [serverOptions, selected, value.target_document_id]);
  const showTarget = value.change_type !== "CREATE";
  // RETIRE obsoletes immediately on implement — the backend ignores proposed_effective_from for it, so
  // offering a date would mislead (Codex #8). Only REVISE/CREATE schedule a cutover off it.
  const showEffectiveDate = value.change_type !== "RETIRE";

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
              // RETIRE ignores the effective date — clear it on switch so a stale value isn't sent
              proposed_effective_from: v === "RETIRE" ? null : value.proposed_effective_from,
            })
          }
          data={(Object.entries(CHANGE_TYPE_LABEL) as [DcrChangeType, string][]).map(
            ([val, label]) => ({
              value: val,
              label,
            }),
          )}
        />
      </div>

      {showTarget && (
        <Select
          label="Target document"
          required
          searchable
          placeholder="Pick the document to revise or retire"
          value={value.target_document_id}
          onChange={(v) => {
            // capture the picked option's label so it survives a later, narrower search
            setSelected(v ? (targetOptions.find((o) => o.value === v) ?? null) : null);
            onChange({ ...value, target_document_id: v });
          }}
          onSearchChange={setSearch}
          // The server already narrowed by `q`; don't re-filter client-side — Mantine's default
          // filter would drop the unioned selected option (its label needn't contain the new term).
          filter={({ options }) => options}
          data={targetOptions}
          rightSection={isFetching ? <Loader size="xs" /> : undefined}
          nothingFoundMessage={
            isError
              ? "Couldn’t load documents — you may not have access."
              : isFetching
                ? "Searching…"
                : "No matching documents"
          }
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

      {showEffectiveDate && (
        <TextInput
          type="date"
          label="Proposed effective from (optional)"
          value={value.proposed_effective_from ?? ""}
          onChange={(e) =>
            onChange({ ...value, proposed_effective_from: e.currentTarget.value || null })
          }
        />
      )}
    </Stack>
  );
}
