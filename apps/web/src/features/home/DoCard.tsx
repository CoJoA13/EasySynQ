import type { ReactNode } from "react";
import { useAckCount } from "../../app/shell/useAckCount";
import { useDriftStatus } from "../drift/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { driftRag, driftStatusText, worstRag, type Rag } from "./rag";

// DO (Cl 7–8): controlled-document integrity (mirror + blob drift) + superseded copies still in
// circulation + the caller's acknowledgements (self-scoped — DO stays visible to everyone via acks).
export function DoCard() {
  const dr = useDriftStatus();
  const { count: ackCount, isError: ackError } = useAckCount();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!dr.forbidden && !dr.isError && dr.data) {
    const rag = driftRag(dr.data);
    rags.push(rag);
    lines.push(
      <StatLine
        key="int"
        label={`Mirror & blob integrity — ${driftStatusText(dr.data)}`}
        tone={rag}
      />,
    );
    if (dr.data.superseded_copies.copies > 0) {
      lines.push(
        <StatLine
          key="sc"
          value={dr.data.superseded_copies.copies}
          label="superseded copies in circulation"
          tone="neutral"
        />,
      );
    }
  }
  // Only show the ack line on a real count — an errored read (count 0 on failure) renders nothing, never
  // a misleading "0 acknowledgements" (the silent-zero the TopBar bell also guards against).
  if (!ackError && ackCount > 0) {
    lines.push(
      <StatLine key="ack" value={ackCount} label="acknowledgements awaiting you" tone="neutral" />,
    );
  }

  const allForbidden = dr.forbidden && ackCount === 0;
  const loading = dr.isLoading;

  return (
    <QuadrantCard
      phase="DO"
      clauseLabel="Cl 7–8"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/drift"
      openLabel="Open drift status"
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
