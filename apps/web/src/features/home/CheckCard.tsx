import type { ReactNode } from "react";
import { useAudits } from "../audits/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { NextReviewLine } from "./NextReviewLine";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { coverageRag, openAuditsCount, worstRag, type Rag } from "./rag";

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
  const rags: Rag[] = [];

  if (!au.forbidden && !au.isError && au.data) {
    lines.push(<StatLine key="aud" value={openAuditsCount(au.data)} label="open audits" tone="neutral" />);
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const rag = coverageRag(cl.data.rollup);
    rags.push(rag);
    lines.push(
      <StatLine key="cov" value={`${cl.data.rollup.covered} / ${cl.data.rollup.total}`} label="mandatory clauses covered" tone={rag} />,
    );
  }
  // The next-review line. A forbidden/errored/unset read renders nothing AND contributes no RAG — so a
  // missing/denied cadence read can never drag the CHECK tile red. Only a RAG-bearing review_state does.
  if (!nd.forbidden && !nd.isError && nd.data) {
    lines.push(<NextReviewLine key="nextrev" />);
    if (nd.data.review_state) {
      rags.push(
        nd.data.review_state === "overdue" ? "red" : nd.data.review_state === "due_soon" ? "amber" : "green",
      );
    }
  }

  const allForbidden = au.forbidden && cl.forbidden && nd.forbidden;
  const loading = au.isLoading || cl.isLoading || nd.isLoading;

  return (
    <QuadrantCard
      phase="CHECK"
      clauseLabel="Cl 9"
      rag={rags.length ? worstRag(rags) : null}
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
