import { ScorecardBandShell } from "../../lib/ScorecardBandShell";
import type { ObjectiveRag, ObjectiveScorecard } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { RAG_LABEL, RAG_TONE } from "./labels";

interface Props {
  total: number;
  onTarget: number;
  byRag: ObjectiveScorecard["by_rag"];
}

// The RAG keys carry the count + the canonical status tone (success/warning/danger/neutral) — so each
// scorecard chip routes through StatusBadge: the tone supplies the AA-tuned colour pair AND a non-colour
// glyph, and the "{count} {meaning}" label disambiguates (status is NEVER colour-only, DP-7). The label
// is the MEANING ("1 on track"), never the colour word ("1 green") — S-obj-rag-legibility.
const KEYS: ObjectiveRag[] = ["green", "amber", "red", "unmeasured"];

export function ObjectiveScorecardBand({ total, onTarget, byRag }: Props) {
  return (
    <ScorecardBandShell
      headline={
        <>
          {onTarget} / {total} on target
        </>
      }
    >
      {KEYS.map((k) => (
        <StatusBadge
          key={k}
          tone={RAG_TONE[k]}
          label={`${byRag[k]} ${RAG_LABEL[k].toLowerCase()}`}
          kind="Objectives"
        />
      ))}
    </ScorecardBandShell>
  );
}
