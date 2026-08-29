import { Anchor, Box, Group, Paper, Skeleton, Stack, Text, VisuallyHidden } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import type { QuadrantSignal } from "./rag";

const PHASE_TOKEN: Record<PdcaPhase, string> = {
  PLAN: "plan",
  DO: "do",
  CHECK: "check",
  ACT: "act",
};

// A calm no-access body (the whole tile's reads were forbidden) and a two-line skeleton (still loading).
export const TileNoAccess = () => (
  <Text size="sm" c="dimmed">
    No access to this section&apos;s data.
  </Text>
);
export const TileSkeleton = () => (
  <Stack gap={6}>
    <Skeleton height={14} width="80%" />
    <Skeleton height={14} width="55%" />
  </Stack>
);

// One PDCA region (doc-11 §5.1 "nav of four labeled regions"), rendered as the signal board's
// quadrant card: a TINTED HEADER BAND carrying the phase, its clause range, and that quadrant's
// current signal, over the signal body and exactly one accent Open action (DP-2).
//
// The header's signal is DERIVED (see quadrantSignal in rag.ts) from the same observations the
// StatLines below render, so it cannot drift from them, and it is always a count plus its label
// rather than a judgement. It carries the non-colour glyph too, so the signal survives with colour
// removed (DP-5) — the tint is decoration and is hidden from assistive technology.
export function QuadrantCard({
  phase,
  clauseLabel,
  signal,
  openTo,
  openLabel,
  children,
}: {
  phase: PdcaPhase;
  clauseLabel: string;
  signal: QuadrantSignal | null;
  openTo: string;
  openLabel: string;
  children: ReactNode;
}) {
  const tok = PHASE_TOKEN[phase];
  return (
    // A flex COLUMN, not a block. The header band is a sibling of the body, so a body asking for
    // height:100% resolves against the Paper's full (grid-stretched) height and pushes its own
    // content out through the overflow clip — taking the card's only "Open …" action with it. The
    // clip is still wanted, to keep the tint inside the rounded corner.
    <Paper
      withBorder
      radius="lg"
      role="group"
      aria-label={`${phase} quadrant`}
      style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      {/* Named "<PHASE> signal" rather than reusing the card's own "<PHASE> quadrant" name: the two
          would otherwise both match a /quadrant/ query, and the summary text would collide with the
          identical StatLine label beneath it (Mantine renders both as accessible names). */}
      <Box
        role="group"
        aria-label={`${phase} signal`}
        style={{
          background: `var(--es-${tok}-header)`,
          padding: "var(--es-space-4) var(--es-space-5)",
        }}
      >
        <Group justify="space-between" align="baseline" wrap="nowrap" gap="sm">
          <Group gap={8} align="baseline" wrap="nowrap">
            <Text span fw={650} style={{ color: `var(--es-${tok}-text)` }}>
              {phase}
            </Text>
            <Text span size="sm" style={{ color: `var(--es-${tok}-clause)` }}>
              {clauseLabel}
            </Text>
          </Group>
          {signal && (
            // Same shape as StatLine: the observation is the accessible NAME and the severity is a
            // visually-hidden DESCRIPTION. Removing the old RAG badge would otherwise have taken the
            // quadrant's severity away from assistive tech and left only a bare number. The severity
            // word is safe to announce here precisely because the text beside it is derived from the
            // observation that produced it, so the two can no longer disagree.
            <Group gap={6} wrap="nowrap" align="baseline">
              <Text
                span
                aria-hidden="true"
                style={{ color: `var(--es-${tok}-text)`, lineHeight: 1 }}
              >
                {signal.glyph}
              </Text>
              <Text span size="sm" fw={500} style={{ color: `var(--es-${tok}-text)` }}>
                {signal.text}
              </Text>
              {/* The glyph is decorative, so the severity would otherwise reach AT only as colour.
                  Safe to state here because the text beside it is derived from the observation that
                  produced it — the two cannot disagree the way a standalone RAG label could. */}
              <VisuallyHidden>Status: {signal.statusLabel}</VisuallyHidden>
            </Group>
          )}
        </Group>
      </Box>
      <Stack gap="sm" p="md" style={{ flex: 1 }}>
        <Stack gap={6} style={{ flex: 1 }}>
          {children}
        </Stack>
        <Anchor component={Link} to={openTo} size="sm">
          {openLabel} <span aria-hidden="true">→</span>
        </Anchor>
      </Stack>
    </Paper>
  );
}
