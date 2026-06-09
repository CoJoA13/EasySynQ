import { Alert, Stack, Text } from "@mantine/core";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import type {
  ConfirmedKind,
  ImportDecisionAction,
  ImportDecisionAfter,
  ImportFile,
  ImportRun,
} from "../../lib/types";
import { BulkActionBar } from "./BulkActionBar";
import { CommitCard } from "./CommitCard";
import { IngestionFacetBar } from "./IngestionFacetBar";
import { ImportPlanBanner } from "./ImportPlanBanner";
import { ItemDetailDrawer } from "./ItemDetailDrawer";
import { MergeMenu } from "./MergeMenu";
import { PreCommitChecklist } from "./PreCommitChecklist";
import { QueueTabs } from "./QueueTabs";
import { RunSummaryTiles, countAt } from "./RunSummaryTiles";
import { TriagePagination } from "./TriagePagination";
import { TriageTable } from "./TriageTable";
import {
  FILES_PAGE_SIZE,
  parseRunUrl,
  queueToFilesQuery,
  type ConfidenceChoice,
  type IngestionQueue,
} from "./filters";
import {
  useBulkDecision,
  useChecklist,
  useCommitRun,
  useDupeClusters,
  useFileDecision,
  useImportFiles,
  useSplit,
  useVersionFamilies,
} from "./hooks";

// The review-face spine. Owns the selection Set, the active drawer file id, and the queue/conf/offset
// URL state; joins clusters/families into the per-row dupe/family maps; threads handlers down to the
// presentational children. Every write generates a fresh Idempotency-Key (one per bulk op).
export function ReviewCockpit({ runId, run }: { runId: string; run: ImportRun }) {
  const [params, setParams] = useSearchParams();
  const { queue, conf, offset } = parseRunUrl(params);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeFileId, setActiveFileId] = useState<string | null>(null);

  const filter = useMemo(() => queueToFilesQuery(queue, conf), [queue, conf]);
  // The "vault" queue has no per-file listing in v1 (it renders an explainer) — don't fetch files for
  // it (the empty {} filter would otherwise pull page 1 of ALL files).
  const filesQuery = useImportFiles(runId, filter, offset, queue !== "vault");
  const clustersQuery = useDupeClusters(runId);
  const familiesQuery = useVersionFamilies(runId);
  const checklistQuery = useChecklist(runId);

  const fileDecision = useFileDecision(runId);
  const bulkDecision = useBulkDecision(runId);
  const commitRun = useCommitRun(runId);
  const splitRun = useSplit(runId);
  const { can } = usePermissions();
  const canCommit = can("import.commit");

  const files = useMemo(() => filesQuery.data?.files ?? [], [filesQuery.data]);
  const review = checklistQuery.data?.review;
  // run.counts has NO `queues` block — derive each tab's badge from the real flat counts (by_band /
  // quarantine) + the folded checklist review stats (undecided). "Already in vault" has no count
  // source in v1 (the tab renders a calm explainer), so it stays 0.
  const queueCounts = useMemo<Record<string, number>>(
    () => ({
      needs: review?.undecided ?? 0,
      medium: countAt(run.counts, "by_band", "MEDIUM"),
      high: countAt(run.counts, "by_band", "HIGH"),
      quarantine: countAt(run.counts, "quarantine"),
      vault: 0,
    }),
    [run.counts, review],
  );

  // dupeMap: each NON-canonical member fileId → the canonical member's review.identifier (or "—").
  const dupeMap = useMemo(() => {
    const idById = new Map<string, string>();
    for (const f of files) if (f.review?.identifier) idById.set(f.id, f.review.identifier);
    const m = new Map<string, string>();
    for (const c of clustersQuery.data?.clusters ?? []) {
      for (const fid of c.member_file_ids) {
        if (fid !== c.canonical_file_id) m.set(fid, idById.get(c.canonical_file_id) ?? "—");
      }
    }
    return m;
  }, [files, clustersQuery.data]);

  // familyMap: each member fileId → its family's ordered-member count.
  const familyMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const fam of familiesQuery.data?.families ?? []) {
      const n = fam.ordered_member_file_ids.length;
      for (const fid of fam.ordered_member_file_ids) m.set(fid, n);
    }
    return m;
  }, [familiesQuery.data]);

  // splitTargetMap: each member fileId → the group it can be split out of (a version family takes
  // priority over a dupe cluster). Used by the drawer's "Split out of group" action.
  const splitTargetMap = useMemo(() => {
    const m = new Map<string, { target_kind: "version_family" | "dupe_cluster"; target_id: string }>();
    for (const fam of familiesQuery.data?.families ?? [])
      for (const fid of fam.ordered_member_file_ids)
        m.set(fid, { target_kind: "version_family", target_id: fam.id });
    for (const c of clustersQuery.data?.clusters ?? [])
      for (const fid of c.member_file_ids)
        if (!m.has(fid)) m.set(fid, { target_kind: "dupe_cluster", target_id: c.id });
    return m;
  }, [familiesQuery.data, clustersQuery.data]);

  // ---- URL patch helpers (the LibraryPage idiom: a queue/conf change resets offset) ----
  const onQueue = useCallback(
    (q: IngestionQueue) => {
      setSelected(new Set()); // a queue change drops a stale cross-queue selection
      setParams((p) => {
        if (q === "needs") p.delete("queue");
        else p.set("queue", q);
        p.delete("offset");
        return p;
      });
    },
    [setParams],
  );
  const onConf = useCallback(
    (c: ConfidenceChoice) => {
      setSelected(new Set()); // a confidence change can hide selected rows — drop the stale selection
      setParams((p) => {
        if (c === "ALL") p.delete("conf");
        else p.set("conf", c);
        p.delete("offset");
        return p;
      });
    },
    [setParams],
  );
  const onOffset = useCallback(
    (o: number) => {
      setParams((p) => {
        if (o > 0) p.set("offset", String(o));
        else p.delete("offset");
        return p;
      });
    },
    [setParams],
  );

  // ---- selection ----
  const onToggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  // Only SELECTABLE rows feed select-all: a non-candidate file (included_candidate === false — which
  // covers quarantine AND any other scan-excluded disposition) is not a commit candidate (the backend
  // 422s an explicit bulk target whose included_candidate is false), so it must never be swept into the
  // selection by "select all on page". This MUST match TriageTable's per-row `selectable` predicate.
  const pageIds = useMemo(
    () => files.filter((f) => f.included_candidate).map((f) => f.id),
    [files],
  );
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const onToggleAllOnPage = useCallback(() => {
    setSelected((prev) => {
      const allSelected = pageIds.length > 0 && pageIds.every((id) => prev.has(id));
      if (allSelected) {
        const next = new Set(prev);
        for (const id of pageIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of pageIds) next.add(id);
      return next;
    });
  }, [pageIds]);

  // ---- writes (a fresh key per user action; one key per bulk op) ----
  const onConfirmKind = useCallback(
    (fileId: string, kind: ConfirmedKind) => {
      fileDecision.mutate({
        fileId,
        body: { action: "accept", after: { kind } },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [fileDecision],
  );
  const onRowAction = useCallback(
    (file: ImportFile, action: ImportDecisionAction) => {
      fileDecision.mutate({
        fileId: file.id,
        body: { action },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [fileDecision],
  );
  const onBulk = useCallback(
    (action: ImportDecisionAction, after?: ImportDecisionAfter) => {
      bulkDecision.mutate({
        body: { action, file_ids: [...selected], after },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [bulkDecision, selected],
  );
  const onBulkConfirmKind = useCallback(
    (kind: ConfirmedKind) => {
      bulkDecision.mutate({
        body: { action: "accept", file_ids: [...selected], after: { kind } },
        idempotencyKey: crypto.randomUUID(),
      });
    },
    [bulkDecision, selected],
  );
  const onAcceptAllHigh = useCallback(() => {
    bulkDecision.mutate({
      body: { action: "accept", selector: { band: "HIGH" } },
      idempotencyKey: crypto.randomUUID(),
    });
  }, [bulkDecision]);

  const checklist = checklistQuery.data;
  const total = queueCounts[queue] ?? 0;
  const hasMore = files.length === FILES_PAGE_SIZE;

  return (
    <Stack gap="md" component="section" aria-label="Review cockpit">
      <RunSummaryTiles run={run} review={review} />
      <ImportPlanBanner />
      <QueueTabs counts={queueCounts} value={queue} onChange={onQueue} />
      <IngestionFacetBar conf={conf} onConf={onConf} />

      {selected.size > 0 && (
        <BulkActionBar
          count={selected.size}
          onBulk={onBulk}
          onConfirmKind={onBulkConfirmKind}
          onAcceptAllHigh={onAcceptAllHigh}
        />
      )}
      {selected.size >= 2 && (
        <MergeMenu runId={runId} selectedFileIds={[...selected]} onDone={() => setSelected(new Set())} />
      )}

      {queue === "vault" ? (
        // "Already in vault" has no per-file listing in v1 — the empty {} filter would otherwise show
        // page 1 of ALL files while the badge says 0. Show the calm registry explainer instead, and
        // do NOT render/paginate the files table for this queue.
        <Alert
          variant="light"
          color="gray"
          title="Already in the vault"
          aria-label="Already in vault"
        >
          <Text size="sm">
            Files that are already controlled in the vault are skipped on commit (they will not be
            re-imported). A per-file listing for this view isn't available yet.
          </Text>
        </Alert>
      ) : (
        <>
          <TriageTable
            files={files}
            dupeMap={dupeMap}
            familyMap={familyMap}
            loading={filesQuery.isLoading}
            selected={selected}
            onToggle={onToggle}
            onToggleAllOnPage={onToggleAllOnPage}
            allOnPageSelected={allOnPageSelected}
            onConfirmKind={onConfirmKind}
            onOpenDetail={setActiveFileId}
            onRowAction={onRowAction}
          />
          <TriagePagination
            offset={offset}
            hasMore={hasMore}
            onOffset={onOffset}
            total={total}
            pageCount={files.length}
          />
        </>
      )}

      {checklist && (
        <>
          <PreCommitChecklist checklist={checklist} onShowBlocker={() => onQueue("needs")} />
          <CommitCard
            checklist={checklist}
            canCommit={canCommit}
            committing={commitRun.isPending}
            onCommit={() => commitRun.mutate()}
          />
        </>
      )}

      <ItemDetailDrawer
        runId={runId}
        fileId={activeFileId}
        onClose={() => setActiveFileId(null)}
        onConfirmKind={(kind) => {
          if (activeFileId) onConfirmKind(activeFileId, kind);
        }}
        onDecision={({ action }) => {
          if (activeFileId)
            fileDecision.mutate({
              fileId: activeFileId,
              body: { action },
              idempotencyKey: crypto.randomUUID(),
            });
        }}
        onSplit={() => {
          if (!activeFileId) return;
          const target = splitTargetMap.get(activeFileId);
          if (target)
            splitRun.mutate({
              body: { ...target, separate_file_ids: [activeFileId] },
              idempotencyKey: crypto.randomUUID(),
            });
        }}
      />
    </Stack>
  );
}
