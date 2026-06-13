import { Alert, Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import type { ReviewInput } from "../../lib/types";
import { INPUT_LABEL } from "./labels";

const RAG_COLOR: Record<string, string> = {
  green: "green",
  amber: "yellow",
  red: "red",
  unmeasured: "gray",
};

// OBJECTIVES_STATUS is the only input type that carries backend RAG — render the RAG band (N9:
// server RAG verbatim, no fabricated status for other inputs). N6: no charts.
function ObjectivesBand({ summary }: { summary: Record<string, unknown> }) {
  const byRag = (summary.by_rag ?? {}) as Record<string, number>;
  return (
    <Stack gap="xs">
      <Text size="sm">
        <Text span fw={600}>{String(summary.on_target ?? 0)}</Text>
        {" / "}
        {String(summary.total ?? 0)} objectives on target
      </Text>
      <Group gap="xs">
        {(["green", "amber", "red", "unmeasured"] as const).map((k) => (
          <Badge key={k} variant="light" color={RAG_COLOR[k]}>
            {`${byRag[k] ?? 0} ${k}`}
          </Badge>
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
  const nested = Object.entries(summary).filter(
    ([, v]) => v !== null && typeof v === "object",
  );
  return (
    <Stack gap="xs">
      <Table withRowBorders={false}>
        <Table.Tbody>
          {entries.map(([k, v]) => (
            <Table.Tr key={k}>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {k.replace(/_/g, " ")}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm" fw={500}>
                  {String(v)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {nested.map(([k, v]) => (
        <Group key={k} gap="xs">
          <Text size="xs" c="dimmed">
            {k.replace(/_/g, " ")}:
          </Text>
          {Object.entries(v as Record<string, unknown>).map(([nk, nv]) => (
            <Badge key={nk} variant="light" color="gray">
              {`${nk.replace(/_/g, " ")} ${String(nv)}`}
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
            {ref.reason ? ref.reason.charAt(0).toUpperCase() + ref.reason.slice(1) : "Not available"}
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
