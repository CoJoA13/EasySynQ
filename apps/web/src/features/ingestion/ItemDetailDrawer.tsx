import { Badge, Button, Divider, Group, Menu, Stack, Text } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { LoadingState } from "../../lib/states";
import type {
  ConfirmedKind,
  ImportClassificationEvidence,
  ImportDecision,
  ImportDecisionAction,
  ImportDedupMembership,
  ImportExtract,
  ImportProposalNode,
} from "../../lib/types";
import { useImportFile } from "./hooks";

// The per-item review detail (DP-3, reuses app/shell/DetailDrawer for focus-trap + Esc + ARIA dialog).
// Presentational: ReviewCockpit (Task 14) owns the active fileId + the decision handlers; this leaf
// reads its own detail (useImportFile, enabled only when fileId is set). The detail read is
// import.review-gated like the page → no separate 403. The decision history comes from the SAME detail
// response (detail.review.decision_history is this file's history) — no separate run-wide /decisions
// fetch. Per-item write actions call the handlers passed down (the cockpit threads them through the
// Task 3-4 hooks), and are shown only for a commit CANDIDATE (included_candidate === true) — a
// non-candidate (quarantine / scan-excluded) is inspect-only since the backend 422s any decision on it.
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
  // The detail endpoint already returns THIS file's decision history under review.decision_history —
  // read it from there (guard under noUncheckedIndexedAccess) rather than fetching the whole run log.
  const history: ImportDecision[] = detail?.review?.decision_history ?? [];
  // A non-candidate (quarantine / scan-excluded) file can't take a per-item decision (the backend 422s
  // any decision/confirm/split on it) → render inspect-only. `detail` is undefined while loading.
  const isCandidate = detail?.included_candidate === true;

  return (
    <DetailDrawer opened={fileId !== null} onClose={onClose} title="Item detail">
      {isLoading || !detail ? (
        <LoadingState label="Loading item detail" />
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

          {/* Per-item actions — call the handlers the cockpit threads through the hooks (Task 3-4).
              Shown ONLY for a commit candidate; a non-candidate (quarantine / scan-excluded) gets an
              inspect-only note since every decision/confirm/split would 422. */}
          {isCandidate ? (
            <Group gap="xs">
              <Button
                size="xs"
                aria-label="Accept item"
                onClick={() => onDecision({ action: "accept" })}
              >
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
              {/* R10: kind is an always-human confirm — offer BOTH Document and Record (a "Confirm
                  kind" that always posted DOCUMENT couldn't classify a record). Mirrors KindCell. */}
              <Menu position="bottom-start" withinPortal>
                <Menu.Target>
                  <Button
                    size="xs"
                    variant="light"
                    aria-label="Confirm kind"
                    rightSection={<span aria-hidden="true">▾</span>}
                  >
                    Confirm kind
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Item onClick={() => onConfirmKind("DOCUMENT")}>Document</Menu.Item>
                  <Menu.Item onClick={() => onConfirmKind("RECORD")}>Record</Menu.Item>
                </Menu.Dropdown>
              </Menu>
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
          ) : (
            <Text size="sm" c="dimmed">
              Inspect-only — this file is not a commit candidate
              {detail.scan_flags.disposition === "quarantine" ? " (quarantined)" : ""}, so no
              decision can be recorded against it.
            </Text>
          )}

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
