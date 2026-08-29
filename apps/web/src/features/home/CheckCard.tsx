import type { ReactNode } from "react";
import { useAudits } from "../audits/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { NextReviewLine, nextReviewObservation } from "./NextReviewLine";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { coverageRag, openAuditsCount, quadrantSignal, type QuadrantObservation } from "./rag";

// CHECK (Cl 9): open internal audits (informational count) + ★ mandatory-clause coverage (the RAG signal)
// + the management-review cadence (clause 9.3, N9 status-against-a-rule).
// Open-NC findings are deferred (no org-wide findings endpoint; spec §2).
export function CheckCard() {
  const au = useAudits();
  const cl = useComplianceChecklist();
  // NextReviewLine reads this same hook; react-query dedups the identical query key, so this second
  // call adds NO network request — it only lets the tile fold the cadence RAG into worstRag.
  const nd = useMgmtReviewNextDue();

  const lines: ReactNode[] = [];
  // Each rendered line also records its observation, so the header folds from the tile itself.
  const obs: QuadrantObservation[] = [];

  if (!au.forbidden && !au.isError && au.data) {
    const open = openAuditsCount(au.data);
    obs.push({ value: open, label: "open audits", rag: "neutral" });
    lines.push(<StatLine key="aud" value={open} label="open audits" tone="neutral" />);
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const rag = coverageRag(cl.data.rollup);
    const value = `${cl.data.rollup.covered} / ${cl.data.rollup.total}`;
    obs.push({ value, label: "mandatory clauses covered", rag });
    lines.push(<StatLine key="cov" value={value} label="mandatory clauses covered" tone={rag} />);
  }
  // The next-review line. A forbidden/errored/unset read renders nothing AND contributes no RAG — so a
  // missing/denied cadence read can never drag the CHECK tile red. Only a RAG-bearing review_state does.
  if (!nd.forbidden && !nd.isError && nd.data) {
    lines.push(<NextReviewLine key="nextrev" />);
    // Folded from the SAME function NextReviewLine renders from, so the header states exactly the
    // line the tile shows. Deriving it here from `review_state` alone ignored the not-configured
    // and none-released branches and invented a label the tile never displays.
    const review = nextReviewObservation(nd);
    if (review) obs.push(review);
  }

  const allForbidden = au.forbidden && cl.forbidden && nd.forbidden;
  const loading = au.isLoading || cl.isLoading || nd.isLoading;

  return (
    <QuadrantCard
      phase="CHECK"
      clauseLabel="Cl 9"
      // The header asserts NOTHING while the body cannot show anything. Without this gate a tile
      // rendering TileNoAccess still announced a signal folded from a read the body suppresses —
      // surfacing in the header exactly the count the tile is declining to show.
      signal={allForbidden || loading || obs.length === 0 ? null : quadrantSignal(obs)}
      openTo="/audits"
      openLabel="Open audits"
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
