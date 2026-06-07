import { Alert, Button, Group, Pill, Select, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useClauses } from "../../app/shell/useClauses";
import { ApiError } from "../../lib/api";
import { useClauseMappings, useMapClause, useUnmapClause } from "./hooks";

// The clause-mapping picker (reused by the New-Document wizard's clause step AND the drawer's Author
// actions). submit-review 422s without ≥1 clause mapping, so this is the load-bearing precondition.
// The current mapping COUNT is read by the parent from the same ["clause-mappings", id] query (cache),
// so the submit gate and this picker never diverge.
function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Could not update clause mappings.";
}

export function ClauseMapper({ documentId }: { documentId: string }) {
  const { data: clauses } = useClauses();
  const { data: mappings } = useClauseMappings(documentId, true);
  const mapClause = useMapClause();
  const unmapClause = useUnmapClause();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mappedIds = new Set((mappings ?? []).map((m) => m.clause_id));
  const options = (clauses ?? [])
    .filter((c) => !mappedIds.has(c.id))
    .map((c) => ({
      value: c.id,
      label: `${c.number} — ${c.title}${c.is_mandatory_star ? " ★" : ""}`,
    }));

  async function add() {
    if (!selected) return;
    setError(null);
    try {
      await mapClause.mutateAsync({ documentId, clauseId: selected });
      setSelected(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function remove(clauseId: string) {
    setError(null);
    try {
      await unmapClause.mutateAsync({ documentId, clauseId });
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Map this document to at least one ISO 9001 clause before submitting for review.
      </Text>
      {error && (
        <Alert color="red" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {(mappings?.length ?? 0) > 0 ? (
        <Pill.Group>
          {(mappings ?? []).map((m) => (
            <Pill
              key={m.id}
              withRemoveButton
              onRemove={() => void remove(m.clause_id)}
              aria-label={`Clause ${m.clause_number}`}
            >
              {m.clause_number}
            </Pill>
          ))}
        </Pill.Group>
      ) : (
        <Text size="sm">No clauses mapped yet.</Text>
      )}
      <Group align="flex-end" gap="sm">
        <Select
          label="Add a clause"
          placeholder="Search clauses"
          searchable
          data={options}
          value={selected}
          onChange={setSelected}
          style={{ flex: 1 }}
        />
        <Button
          variant="light"
          loading={mapClause.isPending}
          disabled={!selected}
          onClick={() => void add()}
        >
          Add
        </Button>
      </Group>
    </Stack>
  );
}
