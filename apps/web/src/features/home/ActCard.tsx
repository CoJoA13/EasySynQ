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
  quadrantSignal,
  type QuadrantObservation,
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
  // Every rendered StatLine also records the observation behind it, so the header signal is folded
  // from exactly what the tile shows rather than computed a second way (S-ui-3).
  const obs: QuadrantObservation[] = [];

  if (!ca.forbidden && !ca.isError && ca.data) {
    const n = capasOpenCount(ca.data);
    const rag = countRag(n, "amber");
    // U14: this KPI counts client-side over the CAPA register, whose scan window is capped.
    // When the window filled, the count is a FLOOR — say so rather than under-report a
    // compliance number as if it were exact.
    const value = ca.truncated ? `${n}+` : n;
    obs.push({ value, label: "CAPAs open", rag });
    lines.push(<StatLine key="capa" value={value} label="CAPAs open" tone={rag} />);
  }
  if (!nc.forbidden && !nc.isError && nc.data) {
    const n = ncrsAwaitingCount(nc.data);
    const rag = countRag(n, "red");
    obs.push({ value: n, label: "NCRs awaiting disposition", rag });
    lines.push(<StatLine key="ncr" value={n} label="NCRs awaiting disposition" tone={rag} />);
  }
  if (!co.forbidden && !co.isError && co.data) {
    const n = complaintsAwaitingCount(co.data);
    const rag = countRag(n, "amber");
    obs.push({ value: n, label: "complaints awaiting triage", rag });
    lines.push(<StatLine key="comp" value={n} label="complaints awaiting triage" tone={rag} />);
  }
  if (!init.forbidden && !init.isError && init.data) {
    const n = initiativesInProgressCount(init.data);
    // Neutral, informational. It IS recorded as an observation so a caller who can see only this
    // line still gets a header that names it — but `neutral` is the lowest severity, so it can never
    // raise the tile above the actionable CAPA/NCR/complaint signals.
    obs.push({
      value: init.truncated ? `${n}+` : n,
      label: "initiatives in progress",
      rag: "neutral",
    });
    lines.push(
      <StatLine
        key="init"
        value={init.truncated ? `${n}+` : n}
        label="initiatives in progress"
        tone="neutral"
      />,
    );
  }

  // ACT no-access is governed by the ACTIONABLE reads only — NOT the initiatives read. The initiatives
  // list endpoint is auth-only / filter-not-403 (api/improvement.py): a caller with no improvement.read
  // gets an empty 200, never a 403, so `init.forbidden` is ~never true. Folding it into allForbidden
  // would make a no-ACT-access user (all three actionable reads 403) render a misleading
  // "0 initiatives in progress" instead of TileNoAccess. The init line is purely additive — when
  // allForbidden wins, the ternary below shows TileNoAccess and the pushed line is never rendered.
  const allForbidden = ca.forbidden && nc.forbidden && co.forbidden;
  const loading = ca.isLoading || nc.isLoading || co.isLoading;

  return (
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      // The header asserts NOTHING while the body cannot show anything. Without this gate a tile
      // rendering TileNoAccess still announced a signal folded from a read the body suppresses —
      // surfacing in the header exactly the count the tile is declining to show.
      signal={allForbidden || loading || obs.length === 0 ? null : quadrantSignal(obs)}
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
