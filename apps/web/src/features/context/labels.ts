import type { ContextCategory, ContextClassification, ContextStatus } from "../../lib/types";
import type { Tone } from "../../lib/status";

// Classification — the ISO clause-4.1 spine (internal/external). The glyph carries the distinction in
// greyscale (⌂ inside the org / ◇ outside it) and the label carries the meaning; tone is for grouping
// only, never the sole signal (DP-5 / WCAG 2.2 AA).
export const CLASSIFICATION_LABEL: Record<ContextClassification, string> = {
  internal: "Internal",
  external: "External",
};
export const CLASSIFICATION_GLYPH: Record<ContextClassification, string> = {
  internal: "⌂",
  external: "◇",
};
export const CLASSIFICATION_TONE: Record<ContextClassification, Tone> = {
  internal: "info",
  external: "neutral",
};

// SWOT category — the optional analysis axis. Helpful (Strength/Opportunity) reads success ✓, harmful
// (Weakness/Threat) reads danger ✕ (the standard SWOT helpful/harmful split); the quadrant label +
// the classification glyph disambiguate Strength from Opportunity (both helpful) in greyscale.
export const CATEGORY_LABEL: Record<ContextCategory, string> = {
  strength: "Strength",
  weakness: "Weakness",
  opportunity: "Opportunity",
  threat: "Threat",
};
export const CATEGORY_TONE: Record<ContextCategory, Tone> = {
  strength: "success",
  weakness: "danger",
  opportunity: "success",
  threat: "danger",
};

// Status — active (open / current) reads info ●, closed (retired) reads neutral ○. A closed issue is
// de-emphasized but never deleted (R50: retire by closing).
export const STATUS_LABEL: Record<ContextStatus, string> = {
  active: "Active",
  closed: "Closed",
};
export const STATUS_TONE: Record<ContextStatus, Tone> = {
  active: "info",
  closed: "neutral",
};
