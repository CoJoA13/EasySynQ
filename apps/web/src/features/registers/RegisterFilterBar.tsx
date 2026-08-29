import { Button, Group, TextInput } from "@mantine/core";

import type { RegisterFilterState } from "./registerFilters";
import { hasActiveRegisterFilters } from "./registerFilters";

interface Props {
  value: RegisterFilterState;
  onChange: (next: RegisterFilterState) => void;
}

/**
 * The shared register date window.
 *
 * This is how an auditor reaches entries older than the server's bounded scan window ("show me last
 * quarter") — otherwise unreachable entirely, because the registers load a fixed newest-first
 * slice. The conditions narrow in SQL BEFORE that window (services/common/register_filters.py).
 *
 * Deliberately only the date pair. Each register page already renders its own domain controls
 * (Severity/Stage/Source on CAPA, Type on risks, State on audits), and a second control with the
 * same accessible name both confuses the operator and breaks getByLabelText. The API accepts the
 * richer per-register facets for API consumers; add one here when a page has a control to give it.
 */
export function RegisterFilterBar({ value, onChange }: Props) {
  const set = (patch: Partial<RegisterFilterState>) => onChange({ ...value, ...patch });

  return (
    <Group gap="xs" align="flex-end" wrap="wrap" mb="md">
      <TextInput
        type="date"
        size="xs"
        label="Created from"
        value={value.createdFrom ?? ""}
        onChange={(e) => set({ createdFrom: e.currentTarget.value })}
      />
      <TextInput
        type="date"
        size="xs"
        label="Created to"
        value={value.createdTo ?? ""}
        onChange={(e) => set({ createdTo: e.currentTarget.value })}
      />
      {hasActiveRegisterFilters(value) && (
        <Button size="xs" variant="subtle" onClick={() => onChange({})}>
          Clear filters
        </Button>
      )}
    </Group>
  );
}
