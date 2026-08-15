import { Button, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useEffect, useMemo, useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { humanizeToken } from "../../lib/labels";
import type { RecordUrlState } from "./recordUrlState";
import { useRecordSourceDocuments } from "./hooks";

const RECORD_TYPES = [
  "AUDIT", "AUDIT_FINDING", "CAPA", "COMPETENCE", "CALIBRATION", "MGMT_REVIEW",
  "SUPPLIER_EVAL", "RELEASE", "KPI_READING", "SATISFACTION", "TRACEABILITY",
  "PROPERTY_EVENT", "CHANGE", "EVIDENCE", "FILLED_FORM", "COMPLAINT",
];
const DISPOSITIONS = ["ACTIVE", "DUE_FOR_REVIEW", "ON_HOLD", "DISPOSED"];

type Criteria = Omit<RecordUrlState, "cursor">;

export function RecordFilters({
  value,
  onChange,
  onClear,
}: {
  value: RecordUrlState;
  onChange: (criteria: Criteria) => void;
  onClear: () => void;
}) {
  const { data: users } = useUserDirectory();
  const [search, setSearch] = useState(value.q ?? "");
  const [sourceSearch, setSourceSearch] = useState("");
  const [settledSearch] = useDebouncedValue(search, 150);
  const [settledSourceSearch] = useDebouncedValue(sourceSearch, 150);
  const sourceDocuments = useRecordSourceDocuments(settledSourceSearch, Boolean(settledSourceSearch));

  useEffect(() => {
    setSearch(value.q ?? "");
  }, [value.q]);
  useEffect(() => {
    if (settledSearch !== (value.q ?? "")) onChange({ ...value, q: settledSearch || undefined });
  }, [settledSearch, value, onChange]);

  const sourceOptions = useMemo(() => {
    const listed = (sourceDocuments.data?.data ?? []).map((document) => ({
      value: document.id,
      label: `${document.identifier ?? "Document"} — ${document.title}`,
    }));
    if (value.source_document_id && !listed.some((item) => item.value === value.source_document_id)) {
      return [{ value: value.source_document_id, label: "Selected item unavailable" }, ...listed];
    }
    return listed;
  }, [sourceDocuments.data, value.source_document_id]);

  const userOptions = (users ?? []).map((user) => ({ value: user.id, label: user.display_name ?? user.id }));
  const typeOptions = RECORD_TYPES.map((item) => ({ value: item, label: humanizeToken(item) }));
  const dispositionOptions = DISPOSITIONS.map((item) => ({ value: item, label: humanizeToken(item) }));
  const chips: Array<[keyof Criteria, string]> = [];
  if (value.record_type) chips.push(["record_type", `Type: ${humanizeToken(value.record_type)}`]);
  if (value.disposition_state) chips.push(["disposition_state", `State: ${humanizeToken(value.disposition_state)}`]);
  if (value.legal_hold) chips.push(["legal_hold", `Legal hold: ${value.legal_hold === "true" ? "Yes" : "No"}`]);
  if (value.source_document_id) chips.push(["source_document_id", "Source document"]);
  if (value.captured_by) chips.push(["captured_by", "Captured by"]);
  if (value.q) chips.push(["q", `Search: ${value.q}`]);

  const control = { miw: 0, w: { base: "100%", sm: "auto" }, mih: 44 } as const;
  const searchControl = {
    ...control,
    w: { base: "100%", sm: 260 },
    style: { flexShrink: 0 },
  } as const;
  const patch = (next: Partial<Criteria>) => onChange({ ...value, ...next });
  return (
    <Stack gap="xs" aria-label="Record filters">
      <Group gap="sm" align="end" wrap="wrap">
        <TextInput
          type="search"
          label="Search records"
          placeholder="Search identifier or title…"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          {...searchControl}
        />
        <Select label="Record type" placeholder="All" data={typeOptions} value={value.record_type ?? null} onChange={(v) => patch({ record_type: v ?? undefined })} clearable {...control} />
        <Select label="Disposition" placeholder="All" data={dispositionOptions} value={value.disposition_state ?? null} onChange={(v) => patch({ disposition_state: v ?? undefined })} clearable {...control} />
        <Select label="Legal hold" placeholder="All" data={[{ value: "true", label: "Yes" }, { value: "false", label: "No" }]} value={value.legal_hold ?? null} onChange={(v) => patch({ legal_hold: v ?? undefined })} clearable {...control} />
        <Select label="Source document" placeholder="All" data={sourceOptions} value={value.source_document_id ?? null} onChange={(v) => patch({ source_document_id: v ?? undefined })} onSearchChange={setSourceSearch} searchable clearable {...control} />
        <Select label="Captured by" placeholder="All" data={userOptions} value={value.captured_by ?? null} onChange={(v) => patch({ captured_by: v ?? undefined })} searchable clearable {...control} />
      </Group>
      {chips.length > 0 && <Group gap="xs" aria-label="Active record filters"><Text size="xs" c="dimmed">Active:</Text>{chips.map(([key, label]) => <Button key={key} variant="light" size="compact-xs" mih={44} aria-label={`Remove filter ${label}`} onClick={() => patch({ [key]: undefined })}>{label}</Button>)}<Button variant="subtle" size="compact-xs" mih={44} onClick={onClear}>Clear all</Button></Group>}
    </Stack>
  );
}
