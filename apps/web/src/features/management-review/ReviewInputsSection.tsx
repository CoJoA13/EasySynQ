import { Alert, Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import type { ReviewInput } from "../../lib/types";
import { humanizeToken } from "../../lib/labels";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import { formatTimestamp } from "../../lib/time";
import { INPUT_LABEL } from "./labels";

// The four RAG bands carried by the OBJECTIVES_STATUS summary (a finite key set so the maps below
// index to a defined value under noUncheckedIndexedAccess).
type RagBand = "green" | "amber" | "red" | "unmeasured";

// RAG → canonical status tone (feature-local — only Tone + glyphs are shared, S-statusbadge-2).
// green→success ✓ (on target), amber→warning ◔ (at risk), red→danger ✕ (off target),
// unmeasured→neutral ○ (no data) — mirrors the objectives-register RAG mapping.
const RAG_TONE: Record<RagBand, Tone> = {
  green: "success",
  amber: "warning",
  red: "danger",
  unmeasured: "neutral",
};

// #2b: the RAG MEANING, not the colour word — so the auditor reads the status on a greyscale export.
const RAG_MEANING: Record<RagBand, string> = {
  green: "on target",
  amber: "at risk",
  red: "off target",
  unmeasured: "unmeasured",
};

// A summary VALUE renderer: an ISO timestamp ("verify_failed_at": "2026-…") is formatted human +
// timezone-explicit so it never reads as a raw machine string; everything else stringifies plainly.
function formatSummaryValue(v: unknown): string {
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) && !Number.isNaN(Date.parse(v))) {
    return formatTimestamp(v);
  }
  return String(v);
}

// OBJECTIVES_STATUS is the only input type that carries backend RAG — render the RAG band (N9:
// server RAG verbatim, no fabricated status for other inputs). N6: no charts.
function ObjectivesBand({ summary }: { summary: Record<string, unknown> }) {
  const byRag = (summary.by_rag ?? {}) as Record<string, number>;
  return (
    <Stack gap="xs">
      <Text size="sm">
        <Text span fw={600}>
          {String(summary.on_target ?? 0)}
        </Text>
        {" / "}
        {String(summary.total ?? 0)} objectives on target
      </Text>
      <Group gap="xs">
        {(["green", "amber", "red", "unmeasured"] as const).map((k) => (
          <StatusBadge
            key={k}
            tone={RAG_TONE[k]}
            label={`${byRag[k] ?? 0} ${RAG_MEANING[k]}`}
            kind="Objective status"
          />
        ))}
      </Group>
    </Stack>
  );
}

// Generic calm key/value table for plain-count summaries (audits, ncrs/capas, kpis, process perf).
// Scalar entries (number|string) → a two-column table row.
// Nested object entries → a labeled badge group (e.g. by_close_state, integrity).
// All values rendered as React text nodes — never dangerouslySetInnerHTML (XSS-safe, S-web-6 rule).
function SummaryTable({ summary }: { summary: Record<string, unknown> }) {
  const entries = Object.entries(summary).filter(
    ([, v]) => typeof v === "number" || typeof v === "string",
  );
  const nested = Object.entries(summary).filter(([, v]) => v !== null && typeof v === "object");
  return (
    <Stack gap="xs">
      <Table withRowBorders={false}>
        <Table.Tbody>
          {entries.map(([k, v]) => (
            <Table.Tr key={k}>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {humanizeToken(k)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm" fw={500}>
                  {formatSummaryValue(v)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {nested.map(([k, v]) => (
        <Group key={k} gap="xs">
          <Text size="xs" c="dimmed">
            {humanizeToken(k)}:
          </Text>
          {Object.entries(v as Record<string, unknown>).map(([nk, nv]) => (
            <Badge key={nk} variant="light" color="gray">
              {`${humanizeToken(nk)} ${formatSummaryValue(nv)}`}
            </Badge>
          ))}
        </Group>
      ))}
    </Stack>
  );
}

function InputCard({ input }: { input: ReviewInput }) {
  const ref = input.source_ref;
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Text fw={600}>{INPUT_LABEL[input.input_type]}</Text>
        {input.available && ref.summary ? (
          input.input_type === "OBJECTIVES_STATUS" ? (
            <ObjectivesBand summary={ref.summary} />
          ) : (
            <SummaryTable summary={ref.summary} />
          )
        ) : (
          <Alert color="gray" variant="light">
            {/* The backend reason already reads "not available (…)"; render it (capitalized) rather
                than prefixing a second "Not available" — avoids the doubled copy (S-mr-2 smoke catch). */}
            {ref.reason
              ? ref.reason.charAt(0).toUpperCase() + ref.reason.slice(1)
              : "Not available"}
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

export function ReviewInputsSection({ inputs }: { inputs: ReviewInput[] }) {
  const ordered = [...inputs].sort((a, b) => a.position - b.position);
  return (
    <Stack gap="sm">
      <Title order={3}>Review inputs (9.3.2)</Title>
      {ordered.map((i) => (
        <InputCard key={i.id} input={i} />
      ))}
    </Stack>
  );
}
