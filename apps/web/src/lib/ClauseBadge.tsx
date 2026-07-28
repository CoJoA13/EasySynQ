import { Badge, VisuallyHidden } from "@mantine/core";

// The one clause-chip representation used on document discovery and register surfaces. Mandatory
// clauses retain the familiar star while the explicit accessible name carries its meaning.
export function ClauseBadge({ clause, starred = false }: { clause: string; starred?: boolean }) {
  return (
    <Badge variant="outline" color="var(--es-accent)" size="sm" data-clause-badge>
      <VisuallyHidden>
        Clause {clause}
        {starred ? ", mandatory" : ""}
      </VisuallyHidden>
      <span aria-hidden="true">
        {starred ? "★ " : ""}
        {clause}
      </span>
    </Badge>
  );
}
