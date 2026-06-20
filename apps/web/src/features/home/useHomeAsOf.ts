import type { DriftStatus } from "../../lib/types";
import { useAudits } from "../audits/hooks";
import { useCapas, useComplaints, useNcrs } from "../capa/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useDriftStatus } from "../drift/hooks";
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { useObjectiveScorecard } from "../objectives/hooks";
import { useRiskSummary } from "../risk/hooks";
import { useMyTasks } from "./hooks";

// The page-wide "as of" for the QMS-health dashboard (critique #2b / P2). Home composes many
// independent reads that each degrade on their own; the honest single freshness stamp is the OLDEST
// successful fetch among them — "nothing shown here is staler than this", which is the trust signal
// Olsen needs. These hooks share react-query cache KEYS with the tiles, so subscribing here triggers
// NO extra network (the tiles already fetched). A forbidden/errored/never-loaded read contributes no
// timestamp (it isn't `isSuccess`), so a denied tile can't drag the stamp to 0. Returns null until at
// least one read has loaded.
// The pure reducer (extracted for direct unit coverage): the OLDEST `dataUpdatedAt` among reads that
// actually loaded. `isSuccess` excludes forbidden/errored/loading reads; `> 0` excludes the never-
// fetched default. Math.MIN (not max) is the trust signal — "nothing shown is staler than this".
export function oldestStamp(
  reads: ReadonlyArray<{ isSuccess: boolean; dataUpdatedAt: number }>,
): number | null {
  const stamps = reads
    .filter((r) => r.isSuccess && r.dataUpdatedAt > 0)
    .map((r) => r.dataUpdatedAt);
  return stamps.length ? Math.min(...stamps) : null;
}

// The drift/integrity tile's TRUE freshness is the last SCAN's `finished_at`, NOT when Home fetched
// the status (Codex #144 P2) — using the fetch time would claim "just now" days after a scan ran,
// overstating the currency of the one signal whose whole point is provable currency. The integrity
// signal is only as fresh as its STALEST scan, so take the oldest finished MIRROR/BLOB_REHASH scan;
// 0 (no finished scan yet) lets `oldestStamp` drop it (a never-run scan reports no freshness).
export function driftScanFreshness(data: DriftStatus | undefined): number {
  if (!data) return 0;
  const times = [data.scans.MIRROR?.finished_at, data.scans.BLOB_REHASH?.finished_at]
    .filter((t): t is string => !!t)
    .map((t) => Date.parse(t));
  return times.length ? Math.min(...times) : 0;
}

export function useHomeAsOf(): number | null {
  const drift = useDriftStatus();
  const reads = [
    useObjectiveScorecard(),
    useRiskSummary(),
    useComplianceChecklist(),
    // Drift contributes its last-scan freshness, not the fetch time (see driftScanFreshness).
    { isSuccess: drift.isSuccess, dataUpdatedAt: driftScanFreshness(drift.data) },
    useAudits(),
    useMgmtReviewNextDue(),
    useCapas(),
    useNcrs(),
    useComplaints(),
    useMyTasks(),
  ];
  return oldestStamp(reads);
}
