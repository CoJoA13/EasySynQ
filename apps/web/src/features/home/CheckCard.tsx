import type { ReactNode } from "react";
import { useAudits } from "../audits/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { coverageRag, openAuditsCount, worstRag, type Rag } from "./rag";

// CHECK (Cl 9): open internal audits (informational count) + ★ mandatory-clause coverage (the RAG signal).
// Open-NC findings are deferred (no org-wide findings endpoint; spec §2).
export function CheckCard() {
  const au = useAudits();
  const cl = useComplianceChecklist();

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

  const allForbidden = au.forbidden && cl.forbidden;
  const loading = au.isLoading || cl.isLoading;

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
