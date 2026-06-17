import type { ReactNode } from "react";
import { useCapas, useComplaints, useNcrs } from "../capa/hooks";
import { useInitiatives } from "../improvement/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import {
  capasOpenCount,
  complaintsAwaitingCount,
  countRag,
  initiativesInProgressCount,
  ncrsAwaitingCount,
  worstRag,
  type Rag,
} from "./rag";

// ACT (Cl 10): open CAPAs (amber when >0) + NCRs awaiting disposition (red when >0) + complaints awaiting
// triage (amber when >0) + improvement initiatives in progress (neutral, informational). Tile RAG = worst
// of the actionable signals — the initiatives line contributes none (improvement activity never reds the tile).
export function ActCard() {
  const ca = useCapas();
  const nc = useNcrs();
  const co = useComplaints();
  const init = useInitiatives();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!ca.forbidden && !ca.isError && ca.data) {
    const n = capasOpenCount(ca.data);
    const rag = countRag(n, "amber");
    rags.push(rag);
    lines.push(<StatLine key="capa" value={n} label="CAPAs open" tone={rag} />);
  }
  if (!nc.forbidden && !nc.isError && nc.data) {
    const n = ncrsAwaitingCount(nc.data);
    const rag = countRag(n, "red");
    rags.push(rag);
    lines.push(<StatLine key="ncr" value={n} label="NCRs awaiting disposition" tone={rag} />);
  }
  if (!co.forbidden && !co.isError && co.data) {
    const n = complaintsAwaitingCount(co.data);
    const rag = countRag(n, "amber");
    rags.push(rag);
    lines.push(<StatLine key="comp" value={n} label="complaints awaiting triage" tone={rag} />);
  }
  if (!init.forbidden && !init.isError && init.data) {
    const n = initiativesInProgressCount(init.data);
    // Neutral, informational — deliberately NOT pushed to `rags` (improvement activity never reds/drags
    // the ACT tile; the tile RAG stays the worst of the actionable CAPA/NCR/complaint signals).
    lines.push(<StatLine key="init" value={n} label="initiatives in progress" tone="neutral" />);
  }

  const allForbidden = ca.forbidden && nc.forbidden && co.forbidden && init.forbidden;
  const loading = ca.isLoading || nc.isLoading || co.isLoading || init.isLoading;

  return (
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/capa"
      openLabel="Open CAPA & NCR"
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
