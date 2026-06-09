import { Button, Checkbox, Group, Skeleton, Stack, Table, Text } from "@mantine/core";
import type {
  ConfirmedKind,
  ImportDecisionAction,
  ImportFile,
} from "../../lib/types";
import { ConfidenceCell } from "./ConfidenceCell";
import { IdentifierCell } from "./IdentifierCell";
import { KindCell } from "./KindCell";
import { TypeCell } from "./TypeCell";

// COLUMNS = 9: 1 select + 1 source + 1 identifier + 1 kind + 1 type + 1 clause + 1 process + 1 confidence + 1 action
// The quarantine row spans 6 middle columns (identifier..confidence).

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The paged triage grid. Presentational: selection, the dupe/family join maps, and every handler
// arrive as props from ReviewCockpit (Task 14). A quarantine row (scan_flags.disposition ===
// "quarantine") renders simplified — filename + a reason cell spanning the classification columns,
// no classification cells, no Accept. Every array/Map access degrades to a defined fallback
// (noUncheckedIndexedAccess). Checkbox aria-labels are distinct per row ("Select <filename>") and
// the header ("Select all on page") so getByLabelText stays single-match (the S-web-6 lesson).
export function TriageTable({
  files,
  dupeMap,
  familyMap,
  loading,
  selected,
  onToggle,
  onToggleAllOnPage,
  allOnPageSelected,
  onConfirmKind,
  onOpenDetail,
  onRowAction,
}: {
  files: ImportFile[];
  dupeMap: Map<string, string>;
  familyMap: Map<string, number>;
  loading: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onToggleAllOnPage: () => void;
  allOnPageSelected: boolean;
  onConfirmKind: (fileId: string, kind: ConfirmedKind) => void;
  onOpenDetail: (fileId: string) => void;
  onRowAction: (file: ImportFile, action: ImportDecisionAction) => void;
}) {
  if (loading) {
    return (
      <Stack gap="xs" role="status" aria-label="Loading files">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={44} />
        ))}
      </Stack>
    );
  }
  if (files.length === 0) {
    return <Text c="dimmed">Nothing in this queue.</Text>;
  }

  return (
    <Table highlightOnHover stickyHeader verticalSpacing="sm" aria-label="Triage queue">
      <Table.Thead>
        <Table.Tr>
          <Table.Th w={36}>
            <Checkbox
              aria-label="Select all on page"
              checked={allOnPageSelected}
              onChange={onToggleAllOnPage}
            />
          </Table.Th>
          <Table.Th scope="col">Source file</Table.Th>
          <Table.Th scope="col">Proposed identifier</Table.Th>
          <Table.Th scope="col">Kind</Table.Th>
          <Table.Th scope="col">Type</Table.Th>
          <Table.Th scope="col" ta="center">
            Clause
          </Table.Th>
          <Table.Th scope="col">Process</Table.Th>
          <Table.Th scope="col">Confidence</Table.Th>
          <Table.Th scope="col">Action</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {files.map((file) => {
          const isQuarantine = file.scan_flags.disposition === "quarantine";
          const memberCount = familyMap.get(file.id);
          const meta = [file.rel_path, humanSize(file.size_bytes)];
          if (memberCount !== undefined && memberCount > 0) {
            meta.push(`${memberCount} versions in family`);
          }
          const sourceCell = (
            <div>
              <Text size="sm" fw={500}>
                {file.filename}
              </Text>
              <Text size="xs" c="dimmed" ff="monospace">
                {meta.join(" · ")}
              </Text>
            </div>
          );

          if (isQuarantine) {
            const reason = file.scan_flags.reason ?? "unreadable";
            return (
              <Table.Tr key={file.id}>
                <Table.Td>
                  {/* A quarantined file is not a commit candidate (the backend 422s an explicit bulk
                      target whose included_candidate is false) → its checkbox is disabled, never
                      toggled, so a select-all can't sweep it into the selection. */}
                  <Checkbox
                    aria-label={`Select ${file.filename}`}
                    checked={false}
                    disabled
                    readOnly
                  />
                </Table.Td>
                <Table.Td>{sourceCell}</Table.Td>
                {/* span the 6 classification columns (identifier..confidence) */}
                <Table.Td colSpan={6}>
                  <Text size="sm" c="dimmed">
                    Quarantined: {reason}
                    {file.scan_flags.detail ? ` — ${file.scan_flags.detail}` : ""}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Button variant="subtle" size="compact-sm" onClick={() => onOpenDetail(file.id)}>
                    Open
                  </Button>
                </Table.Td>
              </Table.Tr>
            );
          }

          return (
            <Table.Tr key={file.id}>
              <Table.Td>
                <Checkbox
                  aria-label={`Select ${file.filename}`}
                  checked={selected.has(file.id)}
                  onChange={() => onToggle(file.id)}
                />
              </Table.Td>
              <Table.Td>{sourceCell}</Table.Td>
              <Table.Td>
                <IdentifierCell review={file.review} dupeOf={dupeMap.get(file.id) ?? null} />
              </Table.Td>
              <Table.Td>
                <KindCell
                  review={file.review}
                  classification={file.classification}
                  onConfirm={(kind) => onConfirmKind(file.id, kind)}
                />
              </Table.Td>
              <Table.Td>
                <TypeCell classification={file.classification} />
              </Table.Td>
              <Table.Td ta="center">
                <Text size="sm" ff="monospace">
                  {file.review?.clause_numbers.length
                    ? file.review.clause_numbers.join(", ")
                    : "—"}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {file.review?.process_names.length
                    ? file.review.process_names.join(", ")
                    : "—"}
                </Text>
              </Table.Td>
              <Table.Td>
                <ConfidenceCell classification={file.classification} />
              </Table.Td>
              <Table.Td>
                {/* No row-level "Correct": it would post a `correct` decision with no `after`, which
                    the backend rejects (422 — a correction must change a dimension). Dimensional
                    correction is done via the BulkActionBar's "Correct to type/owner/clause" menus or
                    the detail drawer. */}
                <Group gap={4} wrap="nowrap">
                  <Button variant="subtle" size="compact-sm" onClick={() => onRowAction(file, "accept")}>
                    Accept
                  </Button>
                  <Button
                    variant="subtle"
                    size="compact-sm"
                    onClick={() => onRowAction(file, "exclude")}
                  >
                    Exclude
                  </Button>
                  <Button variant="subtle" size="compact-sm" onClick={() => onOpenDetail(file.id)}>
                    Open
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}
