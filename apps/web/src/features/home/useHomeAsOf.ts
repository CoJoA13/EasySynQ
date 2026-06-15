import { useAudits } from "../audits/hooks";
import { useCapas, useComplaints, useNcrs } from "../capa/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useDriftStatus } from "../drift/hooks";
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { useObjectiveScorecard } from "../objectives/hooks";
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

export function useHomeAsOf(): number | null {
  const reads = [
    useObjectiveScorecard(),
    useComplianceChecklist(),
    useDriftStatus(),
    useAudits(),
    useMgmtReviewNextDue(),
    useCapas(),
    useNcrs(),
    useComplaints(),
    useMyTasks(),
  ];
  return oldestStamp(reads);
}
