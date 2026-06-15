import { Button, Stack, Table, Text, Textarea } from "@mantine/core";
import { useMemo, useState } from "react";
import type { DcrImpact } from "../../lib/types";
import { dimensionLabel } from "./labels";
import { useAnnotateImpact } from "./mutations";

function summarizeAuto(auto: Record<string, unknown> | null): string {
  if (!auto) return "—";
  if (auto.applicable === false) return "Not applicable";
  const processes = Array.isArray(auto.processes) ? auto.processes.length : null;
  if (processes !== null) return `Applicable · ${processes} process${processes === 1 ? "" : "es"}`;
  return "Applicable";
}

// Signature of the SERVER annotations — drives the EditableImpactTable `key` so it remounts (re-seeds)
// whenever the server values genuinely change (a different DCR, a content refetch that changed them,
// or our own post-save cache write), but NOT on an identical background refetch (same string).
function annotationSignature(impact: DcrImpact[]): string {
  return impact.map((i) => `${i.id}=${i.requester_annotation ?? ""}`).join("|");
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
    // Remount (re-seed) on a different DCR or a genuine server-annotation change — never on an
    // identical background refetch (the signature is unchanged). This is the single reset rule; the
    // editor itself holds a plain local draft with no server reconciliation.
    return (
      <EditableImpactTable
        key={`${dcrId}::${annotationSignature(impact)}`}
        impact={impact}
        dcrId={dcrId}
      />
    );
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
            <Table.Td>{dimensionLabel(i.dimension)}</Table.Td>
            <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
            <Table.Td>{i.requester_annotation ?? "—"}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

// Inline-editable Annotation column + one batch Save (gate is the caller's — changeRequest.assess +
// rows-exist + non-terminal). Sends ONLY the changed dimensions (the backend partial merge). The
// parent KEYS this component on the DCR + the server-annotation signature, so it remounts (re-seeds
// the draft from the latest rows) on any genuine server change — a different DCR, a content refetch
// that changed the annotations, or the post-save cache write — and never on an identical background
// refetch. So the draft is just a plain local copy of the rows it mounted with; no reconciliation.
function EditableImpactTable({ impact, dcrId }: { impact: DcrImpact[]; dcrId: string }) {
  const annotate = useAnnotateImpact(dcrId);
  const seed = useMemo(
    () => Object.fromEntries(impact.map((i) => [i.dimension, i.requester_annotation ?? ""])),
    [impact],
  );
  const [draft, setDraft] = useState<Record<string, string>>(seed);

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([dim, v]) => v !== (seed[dim] ?? "")),
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
              <Table.Td>{dimensionLabel(i.dimension)}</Table.Td>
              <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
              <Table.Td>
                <Textarea
                  aria-label={`Annotation for ${dimensionLabel(i.dimension)}`}
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
        onClick={() => annotate.mutate(changed)}
      >
        Save annotations
      </Button>
    </Stack>
  );
}
