import { Select } from "@mantine/core";
import type { ProcessRow } from "../../lib/types";

// The register's process facet (FIX 4) — a standalone Select beside the Library's FacetBar (which
// has no process facet of its own). Reuses the same processes source every other picker in the app
// uses (features/objectives/hooks.ts useProcesses → GET /processes), never a new endpoint. A
// distinct `label`/accessible name ("Process") keeps it apart from FacetBar's own Selects.
export function ProcessSelect({
  processes,
  value,
  onChange,
}: {
  processes: ProcessRow[];
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  const data = processes.map((p) => ({ value: p.id, label: p.name }));
  return (
    <Select
      label="Process"
      placeholder="All"
      data={data}
      value={value ?? null}
      onChange={(v) => onChange(v ?? undefined)}
      clearable
      searchable
      size="sm"
      w={180}
    />
  );
}
