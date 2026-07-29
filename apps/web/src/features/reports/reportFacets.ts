import type { DocumentControlRegister, ProcessRow } from "../../lib/types";

export interface RegisterFacetSource {
  clauseValues: string[];
  processValues: string[];
  processNames: Record<string, string>;
}

export interface RegisterFacetOption {
  value: string;
  label: string;
}

function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

// Facet membership comes only from the unfiltered report rows the caller may read. This keeps a
// delegated report reader inside the exact same report.read + document.read boundary as the table,
// without making GET /clauses or GET /processes an accidental prerequisite for filtering it.
export function deriveRegisterFacetSource(report: DocumentControlRegister): RegisterFacetSource {
  const clauses = new Set<string>();
  const processes = new Set<string>();
  for (const row of report.rows) {
    for (const ref of row.clause_refs) clauses.add(ref.clause);
    for (const processId of row.process_links) processes.add(processId);
  }

  return {
    clauseValues: [...clauses].sort(naturalCompare),
    processValues: [...processes].sort(naturalCompare),
    // A PROCESS-scoped reader gets these names in provenance even when process.read is absent.
    // Other visible process ids safely fall back to their id until the optional catalog is usable.
    processNames: Object.fromEntries(
      (report.provenance.process_scope ?? []).map((process) => [process.id, process.name]),
    ),
  };
}

export function buildProcessFacetOptions(
  source: RegisterFacetSource,
  catalog: readonly Pick<ProcessRow, "id" | "name">[] = [],
): RegisterFacetOption[] {
  const names = new Map(Object.entries(source.processNames));
  for (const process of catalog) names.set(process.id, process.name);

  return source.processValues
    .map((value) => ({ value, label: names.get(value) ?? value }))
    .sort((a, b) => naturalCompare(a.label, b.label) || naturalCompare(a.value, b.value));
}
