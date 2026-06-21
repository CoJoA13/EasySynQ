import type {
  InterestedPartyInfluence,
  InterestedPartyStatus,
  InterestedPartyType,
} from "../../lib/types";
import type { Tone } from "../../lib/status";

// Party type — the ISO clause-4.2 spine (the 7 relevant interested-party categories; NOT NULL on
// every row). The board groups by this axis, so the LABEL carries the meaning; tone is for grouping
// only, never the sole signal (DP-5 / WCAG 2.2 AA). No per-type glyph: the card heading + label carry
// the type (unlike context's 2-way classification, 7 distinct glyphs would be noisy).
export const PARTY_TYPE_LABEL: Record<InterestedPartyType, string> = {
  customer: "Customers",
  regulator: "Regulators",
  supplier: "Suppliers",
  employee: "Employees",
  owner: "Owners",
  community: "Community",
  partner: "Partners",
};
// The singular form for the per-row badge / drawer (the plural reads as a group heading on the board).
export const PARTY_TYPE_SINGULAR: Record<InterestedPartyType, string> = {
  customer: "Customer",
  regulator: "Regulator",
  supplier: "Supplier",
  employee: "Employee",
  owner: "Owner",
  community: "Community",
  partner: "Partner",
};
export const PARTY_TYPE_TONE: Record<InterestedPartyType, Tone> = {
  customer: "info",
  regulator: "neutral",
  supplier: "neutral",
  employee: "neutral",
  owner: "neutral",
  community: "neutral",
  partner: "neutral",
};

// Influence — the optional ORDERED relevance axis (NOT a RAG alarm: a high-influence party is not a
// "problem"). The magnitude rides a colour-blind-safe filled/half/empty ramp (●◐○, distinct from the
// canonical RAG glyph set ✓◔✕●○★); the label carries the level; the tone stays calm (info for high so
// it reads as the relevance signal, neutral otherwise) — never colour alone (DP-5). The "unspecified"
// (NULL influence) case is handled at the call site (a dimmed "Unspecified", no badge).
export const INFLUENCE_LABEL: Record<InterestedPartyInfluence, string> = {
  low: "Low influence",
  medium: "Medium influence",
  high: "High influence",
};
// The bare level word (the board chip's trailing label + the scorecard badges).
export const INFLUENCE_SHORT: Record<InterestedPartyInfluence, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};
export const INFLUENCE_GLYPH: Record<InterestedPartyInfluence, string> = {
  low: "○",
  medium: "◐",
  high: "●",
};
export const INFLUENCE_TONE: Record<InterestedPartyInfluence, Tone> = {
  low: "neutral",
  medium: "neutral",
  high: "info",
};

// Status — active (open / current) reads info ●, closed (retired) reads neutral ○. A closed party is
// de-emphasized but never deleted (R51: retire by closing).
export const STATUS_LABEL: Record<InterestedPartyStatus, string> = {
  active: "Active",
  closed: "Closed",
};
export const STATUS_TONE: Record<InterestedPartyStatus, Tone> = {
  active: "info",
  closed: "neutral",
};
