import type { Tone } from "../../lib/status";
import type { InitiativeSource, InitiativeStage } from "../../lib/types";

// The clause-10.3 initiative lifecycle → label + canonical status tone. The tone supplies BOTH the
// AA-tuned colour pair AND the non-colour glyph via StatusBadge (status is NEVER colour-only, DP-5):
// the text label always carries the meaning. The map mirrors the sibling DCR own-table workflow badge
// (the same R22/R46 doctrine): Completed is `emphasisSuccess` (★) — the substantive "improvement
// landed" milestone over a plain ✓; Closed is the plain `success` (✓) administrative seal; Open is
// `info` (●, raised/not-yet-worked); InProgress is `warning` (◔, in flight); Cancelled is inert
// (`neutral` ○). (Owner-confirmed S-improvement-3 mapping.)
export const INITIATIVE_STAGE_META: Record<InitiativeStage, { label: string; tone: Tone }> = {
  Open: { label: "Open", tone: "info" },
  InProgress: { label: "In progress", tone: "warning" },
  Completed: { label: "Completed", tone: "emphasisSuccess" },
  Closed: { label: "Closed", tone: "success" },
  Cancelled: { label: "Cancelled", tone: "neutral" },
};

// Provenance, NOT a status — kept off the RAG palette so it never reads as a second verdict. The
// short form labels the register column; the long form labels the drawer field.
export const SOURCE_LABEL: Record<InitiativeSource, string> = {
  OFI: "OFI finding",
  review: "Management review",
  manual: "Manual",
};

export const SOURCE_LABEL_LONG: Record<InitiativeSource, string> = {
  OFI: "From an OFI finding",
  review: "From a management-review output",
  manual: "Manually raised",
};
