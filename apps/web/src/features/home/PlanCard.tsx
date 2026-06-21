import type { ReactNode } from "react";
import { useObjectiveScorecard } from "../objectives/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useRiskSummary } from "../risk/hooks";
import { useContextSummary } from "../context/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { countRag, overdueRag, planObjectivesRag, worstRag, type Rag } from "./rag";

// PLAN (Cl 4–7): Quality Objectives on target (server by_rag) + overdue document reviews + the
// high-risk count (clause 6.1; the GOVERNING read-of-record via GET /risks/summary — S-risk-4a).
export function PlanCard() {
  const sc = useObjectiveScorecard();
  const cl = useComplianceChecklist();
  const rk = useRiskSummary();
  const ctx = useContextSummary();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!sc.forbidden && !sc.isError && sc.data) {
    const rag = planObjectivesRag(sc.data.by_rag);
    rags.push(rag);
    lines.push(
      <StatLine
        key="obj"
        value={`${sc.data.on_target} / ${sc.data.total}`}
        label="objectives on target"
        tone={rag}
      />,
    );
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const n = cl.data.rollup.overdue_review;
    const rag = overdueRag(n);
    rags.push(rag);
    lines.push(<StatLine key="rev" value={n} label="document reviews overdue" tone={rag} />);
  }
  if (!rk.forbidden && !rk.isError && rk.data) {
    if (rk.data.published) {
      // A high or critical risk is an action signal (red when >0, else green). The published register
      // is the controlled read-of-record (governing), not the live working satellite.
      const rag = countRag(rk.data.high_risk, "red");
      rags.push(rag);
      lines.push(
        <StatLine key="risk" value={rk.data.high_risk} label="high / critical risks" tone={rag} />,
      );
    } else {
      // No published register yet → an honest neutral line (never a misleading "0 high-risk"); it
      // doesn't drive the headline RAG.
      lines.push(<StatLine key="risk" label="no published risk register yet" tone="neutral" />);
    }
  }
  if (!ctx.forbidden && !ctx.isError && ctx.data) {
    if (ctx.data.published) {
      // Active context issues are informational (the strategic picture, clause 4.1), not an alarm.
      lines.push(
        <StatLine key="ctx" value={ctx.data.active} label="active context issues" tone="neutral" />,
      );
      // Never-reviewed issues are the actionable freshness signal (amber when >0) → drives the RAG.
      if (ctx.data.never_reviewed > 0) {
        const rag = countRag(ctx.data.never_reviewed, "amber");
        rags.push(rag);
        lines.push(
          <StatLine
            key="ctx-rev"
            value={ctx.data.never_reviewed}
            label="context issues never reviewed"
            tone={rag}
          />,
        );
      }
    } else {
      lines.push(<StatLine key="ctx" label="no published context register yet" tone="neutral" />);
    }
  }

  const allForbidden = sc.forbidden && cl.forbidden && rk.forbidden && ctx.forbidden;
  const loading = sc.isLoading || cl.isLoading || rk.isLoading || ctx.isLoading;

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
