import { Button, Group, Select, Stack, Text } from "@mantine/core";
import { useClauses } from "../../app/shell/useClauses";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { EFFECTIVE_BUCKETS, STATES, type UrlFilters } from "./filters";

// The faceted filter bar (Type · Status · Owner · Clause · Effective date) + a removable active-
// filter chip shelf. Pure presentation: the URL facet state comes in via `value`, every change is
// reported via `onChange` (a partial patch; an undefined value clears that facet). Option data is
// pulled from the shared (cached) reference-data hooks.
export function FacetBar({
  value,
  onChange,
  onClear,
}: {
  value: UrlFilters;
  onChange: (patch: Partial<UrlFilters>) => void;
  onClear: () => void;
}) {
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const { data: clauses } = useClauses();

  const typeData = (types ?? []).map((t) => ({ value: t.id, label: t.name }));
  const ownerData = (directory ?? []).map((u) => ({ value: u.id, label: u.display_name ?? u.id }));
  const clauseData = (clauses ?? []).map((c) => ({ value: c.number, label: `${c.number} ${c.title}` }));
  const stateData = STATES.map((s) => ({ value: s, label: s }));
  const effData = EFFECTIVE_BUCKETS.map((b) => ({ value: b.value, label: b.label }));

  const label = (data: { value: string; label: string }[], v: string) =>
    data.find((d) => d.value === v)?.label ?? v;

  const chips: { key: keyof UrlFilters; text: string }[] = [];
  if (value.type) chips.push({ key: "type", text: `Type: ${label(typeData, value.type)}` });
  if (value.state) chips.push({ key: "state", text: `State: ${value.state}` });
  if (value.owner) chips.push({ key: "owner", text: `Owner: ${label(ownerData, value.owner)}` });
  if (value.clause) chips.push({ key: "clause", text: `Clause: ${value.clause}` });
  if (value.eff) chips.push({ key: "eff", text: `Effective: ${label(effData, value.eff)}` });

  return (
    <Stack gap="xs">
      <Group gap="sm" align="flex-end">
        <Select
          label="Type"
          placeholder="All"
          data={typeData}
          value={value.type ?? null}
          onChange={(v) => onChange({ type: v ?? undefined })}
          clearable
          searchable
          size="sm"
          w={180}
        />
        <Select
          label="Status"
          placeholder="All"
          data={stateData}
          value={value.state ?? null}
          onChange={(v) => onChange({ state: v ?? undefined })}
          clearable
          size="sm"
          w={150}
        />
        <Select
          label="Owner"
          placeholder="All"
          data={ownerData}
          value={value.owner ?? null}
          onChange={(v) => onChange({ owner: v ?? undefined })}
          clearable
          searchable
          size="sm"
          w={180}
        />
        <Select
          label="Clause"
          placeholder="All"
          data={clauseData}
          value={value.clause ?? null}
          onChange={(v) => onChange({ clause: v ?? undefined })}
          clearable
          searchable
          size="sm"
          w={210}
        />
        <Select
          label="Effective date"
          placeholder="Any time"
          data={effData}
          value={value.eff ?? null}
          onChange={(v) => onChange({ eff: v ?? undefined })}
          clearable
          size="sm"
          w={170}
        />
      </Group>
      {chips.length > 0 && (
        <Group gap="xs" aria-label="Active filters">
          <Text size="xs" c="dimmed">
            Active:
          </Text>
          {chips.map((c) => (
            <Button
              key={c.key}
              variant="light"
              size="compact-xs"
              rightSection={<span aria-hidden="true">✕</span>}
              aria-label={`Remove filter ${c.text}`}
              onClick={() => onChange({ [c.key]: undefined })}
            >
              {c.text}
            </Button>
          ))}
          <Button variant="subtle" size="compact-xs" onClick={onClear}>
            Clear all
          </Button>
        </Group>
      )}
    </Stack>
  );
}
