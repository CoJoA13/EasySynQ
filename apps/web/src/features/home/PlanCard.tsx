import type { ReactNode } from "react";
import { useObjectiveScorecard } from "../objectives/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { overdueRag, planObjectivesRag, worstRag, type Rag } from "./rag";

// PLAN (Cl 4–7): Quality Objectives on target (server by_rag, read verbatim) + overdue document reviews.
export function PlanCard() {
  const sc = useObjectiveScorecard();
  const cl = useComplianceChecklist();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!sc.forbidden && !sc.isError && sc.data) {
    const rag = planObjectivesRag(sc.data.by_rag);
    rags.push(rag);
    lines.push(
      <StatLine key="obj" value={`${sc.data.on_target} / ${sc.data.total}`} label="objectives on target" tone={rag} />,
    );
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const n = cl.data.rollup.overdue_review;
    const rag = overdueRag(n);
    rags.push(rag);
    lines.push(<StatLine key="rev" value={n} label="document reviews overdue" tone={rag} />);
  }

  const allForbidden = sc.forbidden && cl.forbidden;
  const loading = sc.isLoading || cl.isLoading;

  return (
    <QuadrantCard
      phase="PLAN"
      clauseLabel="Cl 4–7"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/objectives"
      openLabel="Open objectives"
    >
      {allForbidden ? (
        <TileNoAccess />
      ) : lines.length ? (
        lines
      ) : loading ? (
        <TileSkeleton />
      ) : (
        <StatLine label="Couldn't load this section." tone="neutral" />
      )}
    </QuadrantCard>
  );
}
