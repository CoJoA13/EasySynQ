import { TONE_GLYPH, type Tone } from "../../lib/status";
import type { Audit, Capa, Complaint, DriftStatus, Ncr } from "../../lib/types";

// The dashboard's RAG vocabulary. `neutral` = an informational/unscored signal (NOT objectives'
// `unmeasured`, which maps to neutral). N9: every value is status against a coded rule, read at
// render — never an asserted compliance verdict, never stored.
export type Rag = "green" | "amber" | "red" | "neutral";

// Each RAG maps to a canonical status `tone` (lib/status.ts — the one colour + glyph source of truth):
// `tone` drives the StatusBadge colour pair + the non-colour glyph (DP-7); `hue` is the raw functional
// hue for inline (non-badge) glyph marks like StatLine; `label` is the RAG MEANING — not the colour
// word, so a greyscale / colour-blind reader gets the meaning, not just "Green" (S-clarify-1).
export const RAG_META: Record<Rag, { tone: Tone; glyph: string; label: string; hue: string }> = {
  green: {
    tone: "success",
    glyph: TONE_GLYPH.success,
    label: "On track",
    hue: "var(--es-success)",
  },
  amber: {
    tone: "warning",
    glyph: TONE_GLYPH.warning,
    label: "Needs attention",
    hue: "var(--es-warning)",
  },
  red: {
    tone: "danger",
    glyph: TONE_GLYPH.danger,
    label: "Action required",
    hue: "var(--es-danger)",
  },
  neutral: {
    tone: "neutral",
    glyph: TONE_GLYPH.neutral,
    label: "No data",
    hue: "var(--es-text-muted)",
  },
};

const ORDER: Record<Rag, number> = { neutral: 0, green: 1, amber: 2, red: 3 };

// The worst (most severe) RAG among the visible signals; an empty list (all signals hidden) → neutral.
export function worstRag(rags: Rag[]): Rag {
  return rags.reduce<Rag>((acc, r) => (ORDER[r] > ORDER[acc] ? r : acc), "neutral");
}

// Objectives: read the SERVER-computed by_rag verbatim, roll up worst-wins. Never recompute a row's rag.
export function planObjectivesRag(b: {
  green: number;
  amber: number;
  red: number;
  unmeasured: number;
}): Rag {
  if (b.red > 0) return "red";
  if (b.amber > 0) return "amber";
  if (b.green > 0) return "green";
  return "neutral";
}

export function coverageRag(r: { total: number; covered: number; gap: number }): Rag {
  if (r.gap > 0) return "red";
  if (r.covered < r.total) return "amber";
  return "green";
}

export const overdueRag = (n: number): Rag => (n > 0 ? "amber" : "green");

// A count's RAG: green when zero, otherwise the given severity (amber for CAPAs/complaints, red for NCRs).
export const countRag = (n: number, positive: Rag): Rag => (n > 0 ? positive : "green");

export function driftRag(s: DriftStatus): Rag {
  const mirror = s.scans.MIRROR?.status;
  const blob = s.scans.BLOB_REHASH?.status;
  if (s.blob_coverage.failing > 0 || mirror === "DIVERGENT" || blob === "DIVERGENT") return "red";
  if (mirror === "FAILED" || blob === "FAILED") return "amber";
  // Green only when BOTH integrity legs have run and are clean — a null scan is "not yet scanned",
  // so a fresh/partially-configured deploy with one leg pending shows neutral, never a premature green.
  if (mirror === "CLEAN" && blob === "CLEAN") return "green";
  return "neutral";
}

export function driftStatusText(s: DriftStatus): string {
  const rag = driftRag(s);
  if (rag === "green") return "clean";
  if (rag === "amber") return "scan needs attention";
  if (rag === "neutral") return "not yet scanned";
  const f = s.blob_coverage.failing;
  return f > 0 ? `${f} integrity issue${f === 1 ? "" : "s"}` : "divergence detected";
}

export const openAuditsCount = (a: Audit[]): number => a.filter((x) => x.state !== "Closed").length;
export const capasOpenCount = (c: Capa[]): number =>
  c.filter((x) => x.close_state !== "Closed" && x.close_state !== "Rejected").length;
export const ncrsAwaitingCount = (n: Ncr[]): number =>
  n.filter((x) => x.disposition === null).length;
export const complaintsAwaitingCount = (c: Complaint[]): number =>
  c.filter((x) => x.spawned_capa_id === null).length;
