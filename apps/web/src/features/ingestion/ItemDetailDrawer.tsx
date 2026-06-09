import { Badge, Button, Divider, Group, Loader, Stack, Text } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import type {
  ConfirmedKind,
  ImportClassificationEvidence,
  ImportDecision,
  ImportDecisionAction,
  ImportDedupMembership,
  ImportExtract,
  ImportProposalNode,
} from "../../lib/types";
import { useDecisions, useImportFile } from "./hooks";

// The per-item review detail (DP-3, reuses app/shell/DetailDrawer for focus-trap + Esc + ARIA dialog).
// Presentational: ReviewCockpit (Task 14) owns the active fileId + the decision handlers; this leaf
// reads its own detail (useImportFile, enabled only when fileId is set) + the run decision log
// (filtered to this file). The detail read is import.review-gated like the page → no separate 403.
// Per-item actions call the handlers passed down (the cockpit threads them through the Task 3-4 hooks);
// Split is offered only when the file belongs to a cluster/family (server-authoritative — D-4).
export function ItemDetailDrawer({
  runId,
  fileId,
  onClose,
  onConfirmKind,
  onDecision,
  onSplit,
}: {
  runId: string;
  fileId: string | null;
  onClose: () => void;
  onConfirmKind: (kind: ConfirmedKind) => void;
  onDecision: (input: { action: ImportDecisionAction }) => void;
  onSplit: () => void;
}) {
  const { data: detail, isLoading } = useImportFile(runId, fileId);
  const { data: decisionLog } = useDecisions(runId);
  // Filter the append-only run decision log to this file (guard the array under noUncheckedIndexedAccess).
  const history: ImportDecision[] = (decisionLog?.decisions ?? []).filter(
    (d) => d.file_id === fileId,
  );

  return (
    <DetailDrawer opened={fileId !== null} onClose={onClose} title="Item detail">
      {isLoading || !detail ? (
        <Loader />
      ) : (
        <Stack gap="md">
          {/* Header — filename + proposed identifier (DP-5 shape, quiet absence → "—"). */}
          <Stack gap={2}>
            <Text fw={600}>{detail.filename}</Text>
            <Text ff="monospace" size="sm" c="dimmed">
              {detail.review?.effective?.identifier ??
                detail.proposal?.proposed_identifier ??
                "— no identifier"}
            </Text>
            {detail.rel_path !== detail.filename && (
              <Text size="xs" c="dimmed">
                {detail.rel_path}
              </Text>
            )}
          </Stack>

          {/* Per-item actions — call the handlers the cockpit threads through the hooks (Task 3-4). */}
          <Group gap="xs">
            <Button size="xs" aria-label="Accept item" onClick={() => onDecision({ action: "accept" })}>
              Accept
            </Button>
            <Button
              size="xs"
              variant="default"
              aria-label="Exclude item"
              onClick={() => onDecision({ action: "exclude" })}
            >
              Exclude
            </Button>
            <Button
              size="xs"
              variant="default"
              aria-label="Defer item"
              onClick={() => onDecision({ action: "defer" })}
            >
              Defer
            </Button>
            <Button
              size="xs"
              variant="light"
              aria-label="Confirm kind as Document"
              onClick={() => onConfirmKind("DOCUMENT")}
            >
              Confirm kind
            </Button>
            {(detail.dedup.in_version_family ||
              detail.dedup.in_exact_cluster ||
              detail.dedup.in_near_cluster) && (
              <Button
                size="xs"
                variant="default"
                aria-label="Split out of group"
                onClick={onSplit}
              >
                Split out of group
              </Button>
            )}
          </Group>

          <Divider label="Classification" labelPosition="left" />
          <ClassificationEvidence evidence={detail.classification?.evidence} />

          <Divider label="Extraction" labelPosition="left" />
          <ExtractSummary extract={detail.extract} />

          <Divider label="Group membership" labelPosition="left" />
          <DedupSummary dedup={detail.dedup} />

          <Divider label="Proposal" labelPosition="left" />
          <ProposalSummary proposal={detail.proposal} />

          <Divider label="Decision history" labelPosition="left" />
          {history.length === 0 ? (
            <Text size="sm" c="dimmed">
              No decisions yet for this item.
            </Text>
          ) : (
            <Stack gap={4}>
              {history.map((d) => (
                <Group key={d.id} gap="xs" wrap="nowrap">
                  <Badge variant="light" size="sm">
                    {d.action}
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {d.decided_at.slice(0, 10)}
                  </Text>
                </Group>
              ))}
            </Stack>
          )}
        </Stack>
      )}
    </DetailDrawer>
  );
}

// The 4-dimension classifier signals (evidence array is detail-endpoint-only → guard for null/empty).
function ClassificationEvidence({
  evidence,
}: {
  evidence: ImportClassificationEvidence[] | undefined;
}) {
  if (!evidence || evidence.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No classification evidence.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {evidence.map((e, i) => (
        <Group key={`${e.dimension}-${i}`} gap="xs" wrap="nowrap" align="flex-start">
          <Badge variant="outline" size="sm">
            {e.dimension}
          </Badge>
          <Text size="sm">{e.candidate}</Text>
          <Text size="xs" c="dimmed">
            {e.explanation} (weight {e.weight})
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

function ExtractSummary({ extract }: { extract: ImportExtract | null }) {
  if (!extract) {
    return (
      <Text size="sm" c="dimmed">
        Not extracted.
      </Text>
    );
  }
  return (
    <Group gap="lg">
      <Text size="sm">Status: {extract.status}</Text>
      <Text size="sm">{extract.page_count ?? 0} pages</Text>
      {extract.ocr_used && (
        <Text size="sm" c="dimmed">
          OCR
        </Text>
      )}
    </Group>
  );
}

function DedupSummary({ dedup }: { dedup: ImportDedupMembership }) {
  const inGroup = dedup.in_version_family || dedup.in_exact_cluster || dedup.in_near_cluster;
  if (!inGroup) {
    return (
      <Text size="sm" c="dimmed">
        Not part of a group.
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      {dedup.in_version_family && (
        <Text size="sm">
          In a version family{dedup.is_effective ? " (the effective version)" : ""}.
        </Text>
      )}
      {(dedup.in_exact_cluster || dedup.in_near_cluster) && (
        <Text size="sm">
          In a duplicate cluster{dedup.is_canonical ? " (the canonical copy)" : ""}.
        </Text>
      )}
    </Stack>
  );
}

function ProposalSummary({ proposal }: { proposal: ImportProposalNode | null }) {
  if (!proposal) {
    return (
      <Text size="sm" c="dimmed">
        No proposal.
      </Text>
    );
  }
  const conflicts = Object.keys(proposal.conflict_flags);
  return (
    <Stack gap={2}>
      <Text size="sm">Identifier: {proposal.proposed_identifier ?? "—"}</Text>
      {proposal.identifier_source && (
        <Text size="xs" c="dimmed">
          source: {proposal.identifier_source}
        </Text>
      )}
      <Text size="sm">Target path: {proposal.target_ia_path ?? "—"}</Text>
      {conflicts.length > 0 && (
        <Group gap={4}>
          {conflicts.map((c) => (
            <Badge key={c} variant="light" color="var(--es-danger)" size="sm">
              {c}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}
