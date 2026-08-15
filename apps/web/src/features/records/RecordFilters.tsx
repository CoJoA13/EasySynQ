import { Button, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useEffect, useMemo, useRef, useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { humanizeToken } from "../../lib/labels";
import type { RecordUrlState } from "./recordUrlState";
import { useRecordSourceDocuments } from "./hooks";

const RECORD_TYPES = [
  "AUDIT",
  "AUDIT_FINDING",
  "CAPA",
  "COMPETENCE",
  "CALIBRATION",
  "MGMT_REVIEW",
  "SUPPLIER_EVAL",
  "RELEASE",
  "KPI_READING",
  "SATISFACTION",
  "TRACEABILITY",
  "PROPERTY_EVENT",
  "CHANGE",
  "EVIDENCE",
  "FILLED_FORM",
  "COMPLAINT",
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
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceQuery, setSourceQuery] = useState("");
  const [sourceDisplay, setSourceDisplay] = useState("");
  const [settledSearch] = useDebouncedValue(search, 150);
  const [settledSourceQuery] = useDebouncedValue(sourceQuery, 150);
  const sourceDocuments = useRecordSourceDocuments(settledSourceQuery, sourceOpen);

  // Mirror the registerControls guarded-debounce contract. React Router changes the criteria
  // callback identity whenever the URL moves; subscribing the write effect to that callback or the
  // whole value object replays a stale settled search after Clear/chip removal/back navigation.
  const urlSearch = value.q ?? "";
  const urlSearchRef = useRef(urlSearch);
  urlSearchRef.current = urlSearch;
  const searchRef = useRef(search);
  searchRef.current = search;
  const valueRef = useRef(value);
  valueRef.current = value;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const lastWrittenRef = useRef(urlSearch);

  useEffect(() => {
    if (urlSearch !== lastWrittenRef.current) {
      lastWrittenRef.current = urlSearch;
      searchRef.current = urlSearch;
      setSearch(urlSearch);
    }
  }, [urlSearch]);
  useEffect(() => {
    if (settledSearch === searchRef.current && settledSearch !== urlSearchRef.current) {
      lastWrittenRef.current = settledSearch;
      onChangeRef.current({
        ...valueRef.current,
        q: settledSearch || undefined,
      });
    }
  }, [settledSearch]);

  const listedSourceOptions = useMemo(
    () =>
      (sourceDocuments.isSuccess && sourceDocuments.isFetchedAfterMount
        ? (sourceDocuments.data?.data ?? [])
        : []
      ).map((document) => ({
        value: document.id,
        label: `${document.identifier ?? "Document"} — ${document.title}`,
      })),
    [sourceDocuments.data, sourceDocuments.isFetchedAfterMount, sourceDocuments.isSuccess],
  );
  const selectedSourceProjectionRef = useRef<{ value: string; label: string } | null>(null);
  const selectedSourceIdRef = useRef(value.source_document_id);
  if (selectedSourceIdRef.current !== value.source_document_id) {
    selectedSourceIdRef.current = value.source_document_id;
    if (selectedSourceProjectionRef.current?.value !== value.source_document_id) {
      selectedSourceProjectionRef.current = null;
    }
  }
  const listedSelectedSource = listedSourceOptions.find(
    (option) => option.value === value.source_document_id,
  );
  if (!value.source_document_id) {
    selectedSourceProjectionRef.current = null;
  } else if (listedSelectedSource) {
    selectedSourceProjectionRef.current = listedSelectedSource;
  }
  const retainedSelectedSource =
    selectedSourceProjectionRef.current?.value === value.source_document_id
      ? selectedSourceProjectionRef.current
      : null;
  const sourceOptions =
    value.source_document_id && !listedSelectedSource
      ? [
          retainedSelectedSource ?? {
            value: value.source_document_id,
            label: "Selected item unavailable",
          },
          ...listedSourceOptions,
        ]
      : listedSourceOptions;

  const selectedSourceLabel =
    sourceOptions.find((option) => option.value === value.source_document_id)?.label ?? "";
  const selectedSourceLabelRef = useRef(selectedSourceLabel);
  selectedSourceLabelRef.current = selectedSourceLabel;
  const selectionDisplayRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sourceOpen) {
      setSourceDisplay(selectedSourceLabel);
      setSourceQuery("");
    }
  }, [selectedSourceLabel, sourceOpen]);

  const listedUsers = (users ?? []).map((user) => ({
    value: user.id,
    label: user.display_name ?? "User name unavailable",
  }));
  const userOptions =
    value.captured_by && !listedUsers.some((user) => user.value === value.captured_by)
      ? [{ value: value.captured_by, label: "Selected item unavailable" }, ...listedUsers]
      : listedUsers;
  const typeOptions = RECORD_TYPES.map((item) => ({ value: item, label: humanizeToken(item) }));
  const dispositionOptions = DISPOSITIONS.map((item) => ({
    value: item,
    label: humanizeToken(item),
  }));
  const chips: Array<[keyof Criteria, string]> = [];
  if (value.record_type) chips.push(["record_type", `Type: ${humanizeToken(value.record_type)}`]);
  if (value.disposition_state)
    chips.push(["disposition_state", `State: ${humanizeToken(value.disposition_state)}`]);
  if (value.legal_hold)
    chips.push(["legal_hold", `Legal hold: ${value.legal_hold === "true" ? "Yes" : "No"}`]);
  if (value.source_document_id) chips.push(["source_document_id", "Source document"]);
  if (value.captured_by) chips.push(["captured_by", "Captured by"]);
  if (value.q) chips.push(["q", `Search: ${value.q}`]);

  const control = {
    miw: 0,
    w: { base: "100%", sm: "auto" },
    mih: 44,
    styles: { input: { minHeight: 44 } },
  } as const;
  const searchControl = {
    ...control,
    w: { base: "100%", sm: 260 },
    style: { flexShrink: 0 },
  } as const;
  const adoptUrlSearch = (next: string | undefined) => {
    const adopted = next ?? "";
    searchRef.current = adopted;
    lastWrittenRef.current = adopted;
    setSearch(adopted);
  };
  const patch = (next: Partial<Criteria>) => {
    const nextCriteria = { ...value, ...next };
    adoptUrlSearch(nextCriteria.q);
    if ("source_document_id" in next && !next.source_document_id) {
      selectedSourceProjectionRef.current = null;
    }
    onChange(nextCriteria);
  };
  const clearAll = () => {
    adoptUrlSearch(undefined);
    selectedSourceProjectionRef.current = null;
    onClear();
  };
  const truncateLabel = {
    display: "block",
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  } as const;
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
        <Select
          label="Record type"
          placeholder="All"
          data={typeOptions}
          value={value.record_type ?? null}
          onChange={(v) => patch({ record_type: v ?? undefined })}
          clearable
          {...control}
        />
        <Select
          label="Disposition"
          placeholder="All"
          data={dispositionOptions}
          value={value.disposition_state ?? null}
          onChange={(v) => patch({ disposition_state: v ?? undefined })}
          clearable
          {...control}
        />
        <Select
          label="Legal hold"
          placeholder="All"
          data={[
            { value: "true", label: "Yes" },
            { value: "false", label: "No" },
          ]}
          value={value.legal_hold ?? null}
          onChange={(v) => patch({ legal_hold: v ?? undefined })}
          clearable
          {...control}
        />
        <Select
          label="Source document"
          placeholder="All"
          data={sourceOptions}
          value={value.source_document_id ?? null}
          searchValue={sourceDisplay}
          onDropdownOpen={() => {
            setSourceOpen(true);
            setSourceQuery("");
          }}
          onDropdownClose={() => {
            setSourceOpen(false);
            setSourceQuery("");
            setSourceDisplay(selectionDisplayRef.current ?? selectedSourceLabelRef.current);
            selectionDisplayRef.current = null;
          }}
          onSearchChange={(next) => {
            setSourceDisplay(next);
            const selectedDisplay = selectionDisplayRef.current;
            if (sourceOpen && next !== selectedSourceLabelRef.current && next !== selectedDisplay) {
              setSourceQuery(next);
            } else if (next === selectedDisplay) {
              setSourceQuery("");
              selectionDisplayRef.current = null;
            }
          }}
          onChange={(next) => {
            const selected = listedSourceOptions.find((option) => option.value === next) ?? null;
            selectedSourceProjectionRef.current = selected;
            const label = selected?.label ?? "";
            selectionDisplayRef.current = label;
            setSourceDisplay(label);
            setSourceQuery("");
            patch({ source_document_id: next ?? undefined });
          }}
          searchable
          clearable
          {...control}
        />
        <Select
          label="Captured by"
          placeholder="All"
          data={userOptions}
          value={value.captured_by ?? null}
          onChange={(v) => patch({ captured_by: v ?? undefined })}
          searchable
          clearable
          {...control}
        />
      </Group>
      {chips.length > 0 && (
        <Group gap="xs" aria-label="Active record filters" miw={0}>
          <Text size="xs" c="dimmed">
            Active:
          </Text>
          {chips.map(([key, label]) => (
            <Button
              key={key}
              variant="light"
              size="compact-xs"
              mih={44}
              maw="100%"
              style={{ minWidth: 0 }}
              aria-label={`Remove filter ${label}`}
              onClick={() => patch({ [key]: undefined })}
            >
              <Text span style={truncateLabel}>
                {label}
              </Text>
            </Button>
          ))}
          <Button variant="subtle" size="compact-xs" mih={44} onClick={clearAll}>
            Clear all
          </Button>
        </Group>
      )}
    </Stack>
  );
}
