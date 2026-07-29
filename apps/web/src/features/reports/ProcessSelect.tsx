import { Select } from "@mantine/core";
import type { RegisterFacetOption } from "./reportFacets";

// The register's process facet — a standalone Select beside the Library's FacetBar (which has no
// process facet of its own). Options are the process ids present in the caller-visible baseline
// register; labels are optionally enriched from the process catalog or process-scope provenance.
export function ProcessSelect({
  options,
  value,
  onChange,
}: {
  options: RegisterFacetOption[];
  value: string | undefined;
  onChange: (v: string | undefined) => void;
}) {
  return (
    <Select
      label="Process"
      placeholder="All"
      data={options}
      value={value ?? null}
      onChange={(v) => onChange(v ?? undefined)}
      clearable
      searchable
      size="sm"
      w={180}
    />
  );
}
