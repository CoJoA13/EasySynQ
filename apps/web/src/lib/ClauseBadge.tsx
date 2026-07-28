import { Badge } from "@mantine/core";

// The one clause-chip representation used on document discovery and register surfaces. Mandatory
// clauses retain the familiar star while the explicit accessible name carries its meaning.
export function ClauseBadge({ clause, starred = false }: { clause: string; starred?: boolean }) {
  return (
    <Badge
      variant="outline"
      color="var(--es-accent)"
      size="sm"
      aria-label={`Clause ${clause}${starred ? ", mandatory" : ""}`}
      data-clause-badge
    >
      {starred && <span aria-hidden="true">★ </span>}
      {clause}
    </Badge>
  );
}
