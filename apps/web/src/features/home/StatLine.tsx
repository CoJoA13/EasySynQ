import { Group, Text } from "@mantine/core";
import type { Rag } from "./rag";
import { RAG_META } from "./rag";

// One dashboard signal: a tone glyph (DP-7 redundant channel) + an optional bold tabular value + a
// label. Count lines pass a value ("6 / 8"); status lines fold the status into the label and omit value.
export function StatLine({
  value,
  label,
  tone = "neutral",
}: {
  value?: string | number;
  label: string;
  tone?: Rag;
}) {
  const hasValue = value !== undefined && value !== "";
  const name = hasValue ? `${value} ${label}` : label;
  return (
    <Group gap={8} wrap="nowrap" aria-label={name}>
      <Text span c={RAG_META[tone].hue} aria-hidden style={{ lineHeight: 1 }}>
        {RAG_META[tone].glyph}
      </Text>
      <Text size="sm">
        {hasValue && (
          <Text span fw={500} style={{ fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Text>
        )}
        {hasValue ? " " : ""}
        {label}
      </Text>
    </Group>
  );
}
