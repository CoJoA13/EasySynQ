import type { ReactNode } from "react";
import { useObjectiveScorecard } from "../objectives/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useRiskSummary } from "../risk/hooks";
import { useContextSummary } from "../context/hooks";
import { useInterestedPartySummary } from "../interested-parties/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import {
  countRag,
  overdueRag,
  planObjectivesRag,
  quadrantSignal,
  type QuadrantObservation,
} from "./rag";

// PLAN (Cl 4–6, R62): Quality Objectives on target (server by_rag) + overdue document reviews + the
// high-risk count (clause 6.1; the GOVERNING read-of-record via GET /risks/summary — S-risk-4a).
export function PlanCard() {
  const sc = useObjectiveScorecard();
  const cl = useComplianceChecklist();
  const rk = useRiskSummary();
  const ctx = useContextSummary();
  const ip = useInterestedPartySummary();

  const lines: ReactNode[] = [];
  // Each rendered line also records its observation, so the header folds from the tile itself.
  const obs: QuadrantObservation[] = [];

  if (!sc.forbidden && !sc.isError && sc.data) {
    const rag = planObjectivesRag(sc.data.by_rag);
    const value = `${sc.data.on_target} / ${sc.data.total}`;
    obs.push({ value, label: "objectives on target", rag });
    lines.push(<StatLine key="obj" value={value} label="objectives on target" tone={rag} />);
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const n = cl.data.rollup.overdue_review;
    const rag = overdueRag(n);
    obs.push({ value: n, label: "document reviews overdue", rag });
    lines.push(<StatLine key="rev" value={n} label="document reviews overdue" tone={rag} />);
  }
  if (!rk.forbidden && !rk.isError && rk.data) {
    if (rk.data.published) {
      // A high or critical risk is an action signal (red when >0, else green). The published register
      // is the controlled read-of-record (governing), not the live working satellite.
      const rag = countRag(rk.data.high_risk, "red");
      obs.push({ value: rk.data.high_risk, label: "high / critical risks", rag });
      lines.push(
        <StatLine key="risk" value={rk.data.high_risk} label="high / critical risks" tone={rag} />,
      );
    } else {
      // No published register yet → an honest neutral line (never a misleading "0 high-risk"); it
      // doesn't drive the headline RAG.
      obs.push({ label: "no published risk & opportunity register yet", rag: "neutral" });
      lines.push(
        <StatLine key="risk" label="no published risk & opportunity register yet" tone="neutral" />,
      );
    }
  }
  if (!ctx.forbidden && !ctx.isError && ctx.data) {
    if (ctx.data.published) {
      // Active context issues are informational (the strategic picture, clause 4.1), not an alarm.
      obs.push({ value: ctx.data.active, label: "active context issues", rag: "neutral" });
      lines.push(
        <StatLine key="ctx" value={ctx.data.active} label="active context issues" tone="neutral" />,
      );
      // Never-reviewed issues are the actionable freshness signal (amber when >0) → drives the RAG.
      if (ctx.data.never_reviewed > 0) {
        const rag = countRag(ctx.data.never_reviewed, "amber");
        obs.push({ value: ctx.data.never_reviewed, label: "context issues never reviewed", rag });
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
      obs.push({ label: "no published context register yet", rag: "neutral" });
      lines.push(<StatLine key="ctx" label="no published context register yet" tone="neutral" />);
    }
  }
  if (!ip.forbidden && !ip.isError && ip.data) {
    if (ip.data.published) {
      // Active interested parties are informational (the strategic picture, clause 4.2), not an alarm.
      obs.push({ value: ip.data.active, label: "active interested parties", rag: "neutral" });
      lines.push(
        <StatLine
          key="ip"
          value={ip.data.active}
          label="active interested parties"
          tone="neutral"
        />,
      );
      // Never-reviewed parties are the actionable freshness signal (amber when >0) → drives the RAG.
      if (ip.data.never_reviewed > 0) {
        const rag = countRag(ip.data.never_reviewed, "amber");
        obs.push({
          value: ip.data.never_reviewed,
          label: "interested parties never reviewed",
          rag,
        });
        lines.push(
          <StatLine
            key="ip-rev"
            value={ip.data.never_reviewed}
            label="interested parties never reviewed"
            tone={rag}
          />,
        );
      }
    } else {
      obs.push({ label: "no published interested-parties register yet", rag: "neutral" });
      lines.push(
        <StatLine key="ip" label="no published interested-parties register yet" tone="neutral" />,
      );
    }
  }

  const allForbidden =
    sc.forbidden && cl.forbidden && rk.forbidden && ctx.forbidden && ip.forbidden;
  const loading = sc.isLoading || cl.isLoading || rk.isLoading || ctx.isLoading || ip.isLoading;

  return (
    <QuadrantCard
      phase="PLAN"
      clauseLabel="Cl 4–6"
      // The header asserts NOTHING while the body cannot show anything. Without this gate a tile
      // rendering TileNoAccess still announced a signal folded from a read the body suppresses —
      // surfacing in the header exactly the count the tile is declining to show.
      signal={allForbidden || loading || obs.length === 0 ? null : quadrantSignal(obs)}
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
