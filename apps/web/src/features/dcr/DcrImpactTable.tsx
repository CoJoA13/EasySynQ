import { Button, Stack, Table, Text, Textarea } from "@mantine/core";
import { useMemo, useState } from "react";
import type { DcrImpact } from "../../lib/types";
import { useAnnotateImpact } from "./mutations";

function summarizeAuto(auto: Record<string, unknown> | null): string {
  if (!auto) return "—";
  if (auto.applicable === false) return "Not applicable";
  const processes = Array.isArray(auto.processes) ? auto.processes.length : null;
  if (processes !== null) return `Applicable · ${processes} process${processes === 1 ? "" : "es"}`;
  return "Applicable";
}

// Annotation maps are equal iff every dimension's text matches (missing ≡ ""). Drives "is the draft
// pristine?" — whether the user has unsaved edits relative to the baseline it was seeded from.
function sameAnnotations(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) if ((a[k] ?? "") !== (b[k] ?? "")) return false;
  return true;
}

// Read-only: the auto-populated facts + the (frozen) requester annotation. Editing is EditableImpactTable.
export function DcrImpactTable({
  impact,
  editable = false,
  dcrId,
}: {
  impact: DcrImpact[];
  editable?: boolean;
  dcrId?: string;
}) {
  if (impact.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Not yet assessed.
      </Text>
    );
  }
  if (editable && dcrId) {
    return <EditableImpactTable impact={impact} dcrId={dcrId} />;
  }
  return (
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Dimension</Table.Th>
          <Table.Th>System facts</Table.Th>
          <Table.Th>Annotation</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {impact.map((i) => (
          <Table.Tr key={i.id}>
            <Table.Td>{i.dimension}</Table.Td>
            <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
            <Table.Td>{i.requester_annotation ?? "—"}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

// Inline-editable Annotation column + one batch Save (gate is the caller's — changeRequest.assess +
// rows-exist + non-terminal). Sends ONLY the changed dimensions (the backend partial merge).
function EditableImpactTable({ impact, dcrId }: { impact: DcrImpact[]; dcrId: string }) {
  const annotate = useAnnotateImpact(dcrId);
  const seed = useMemo(
    () => Object.fromEntries(impact.map((i) => [i.dimension, i.requester_annotation ?? ""])),
    [impact],
  );
  // `baseline` = the server values the draft is measured against; `draft` = the user's working copy.
  // Adopt fresh server values whenever the draft is PRISTINE (no unsaved edits) and the seed changed
  // — a different DCR, or a content refetch that updated the annotations underneath (Codex P2: a
  // pristine draft must take the refetched values, not re-PUT stale ones). A DIRTY draft is left
  // untouched so a background refetch can't discard typed edits. `changed` is measured against the
  // baseline (not the live seed), so an untouched dimension a concurrent refetch changed is never
  // re-PUT. After a save we reset the baseline to the saved values (the Save onClick), so Save
  // disables. React's render-time "sync state during render" pattern (no effect).
  const [baseline, setBaseline] = useState<Record<string, string>>(seed);
  const [draft, setDraft] = useState<Record<string, string>>(seed);
  if (seed !== baseline && sameAnnotations(draft, baseline)) {
    setBaseline(seed);
    setDraft(seed);
  }

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([dim, v]) => v !== (baseline[dim] ?? "")),
  );
  const hasChanges = Object.keys(changed).length > 0;

  return (
    <Stack gap="sm">
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Dimension</Table.Th>
            <Table.Th>System facts</Table.Th>
            <Table.Th>Annotation</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {impact.map((i) => (
            <Table.Tr key={i.id}>
              <Table.Td>{i.dimension}</Table.Td>
              <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
              <Table.Td>
                <Textarea
                  aria-label={`Annotation for ${i.dimension}`}
                  value={draft[i.dimension] ?? ""}
                  onChange={(e) => {
                    const val = e.currentTarget.value;
                    setDraft((d) => ({ ...d, [i.dimension]: val }));
                  }}
                  autosize
                  minRows={1}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {annotate.isError && (
        <Text size="sm" c="red">
          Couldn't save the annotations. Please try again.
        </Text>
      )}
      <Button
        w="fit-content"
        loading={annotate.isPending}
        disabled={!hasChanges}
        onClick={() => {
          const saved = { ...draft };
          annotate.mutate(changed, { onSuccess: () => setBaseline(saved) });
        }}
      >
        Save annotations
      </Button>
    </Stack>
  );
}
