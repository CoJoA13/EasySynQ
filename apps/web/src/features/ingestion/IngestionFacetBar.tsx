import { SegmentedControl } from "@mantine/core";
import type { ConfidenceChoice } from "./filters";

// The confidence-band facet. Per the contract constraint, band is the ONLY server-filterable dimension
// for the /files list (clause/process/type facets are deferred — not server-filterable), so this bar is
// a single confidence SegmentedControl. The `value`/`onChange` are the ConfidenceChoice values
// (ALL/HIGH/MEDIUM/LOW) with friendlier visible labels; SegmentedControl gives radiogroup semantics.
const CONF_DATA: { value: ConfidenceChoice; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "HIGH", label: "High" },
  { value: "MEDIUM", label: "Medium" },
  { value: "LOW", label: "Low" },
];

export function IngestionFacetBar({
  conf,
  onConf,
}: {
  conf: ConfidenceChoice;
  onConf: (c: ConfidenceChoice) => void;
}) {
  return (
    <SegmentedControl
      value={conf}
      onChange={(v) => onConf(v as ConfidenceChoice)}
      data={CONF_DATA}
      size="sm"
      aria-label="Confidence band"
    />
  );
}
