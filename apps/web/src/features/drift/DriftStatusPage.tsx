import {
  Alert,
  Anchor,
  Card,
  Container,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { Link } from "react-router-dom";
import { AsOf } from "../../lib/AsOf";
import { humanizeToken } from "../../lib/labels";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import { formatTimestamp } from "../../lib/time";
import type { DriftScanStatusValue, DriftScanSummary } from "../../lib/types";
import { useDriftStatus } from "./hooks";

// S-web-8: the admin drift-status read (S-drift-3 / doc 05 §9.1). Pure read — no scan trigger.

// S-statusbadge-2: the scan status maps onto the ONE canonical status system (Tone + glyph come from
// lib/status via StatusBadge — status is NEVER colour-only, DP-5 / DP-7). The label stays the raw enum
// value so the per-card aria-label ("Mirror scan status: CLEAN") is preserved verbatim.
//   • CLEAN     → success ✓ (clean / passed scan).
//   • DIVERGENT → danger  ✕ (mirror tamper/divergence — a red integrity condition; the ▲ glyph is RETIRED).
//   • FAILED    → danger  ✕ (LOCKED owner design-call: a genuine integrity failure — failed blob-verify /
//     mirror-tamper — is the strongest signal for an auditor on a greyscale export). The distinct label
//     ("DIVERGENT" vs "FAILED") keeps them legible despite the shared danger tone.
const META: Record<DriftScanStatusValue, { tone: Tone }> = {
  CLEAN: { tone: "success" },
  DIVERGENT: { tone: "danger" },
  FAILED: { tone: "danger" },
};

// #2b: a localised, timezone-explicit absolute timestamp (replaces the ambiguous iso.slice(0,16) UTC
// wall-clock). The "as of" relative freshness is the AsOf chip in each scan card header.
const fmt = formatTimestamp;

// counts is an OPEN bag (S-drift-3 §10a): render every key generically, sorted — unknown keys are
// additive by contract, so the UI must never destructure a closed set.
function ScanCard({ title, scan }: { title: string; scan: DriftScanSummary | null }) {
  if (!scan) {
    return (
      <Card withBorder>
        <Text fw={600}>{title}</Text>
        <Text size="sm" c="dimmed">
          Never run yet.
        </Text>
      </Card>
    );
  }
  const meta = META[scan.status];
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600}>{title}</Text>
          {/* Canonical status pill — glyph (non-colour channel) + per-card-unique aria-label */}
          <StatusBadge tone={meta.tone} label={scan.status} kind={`${title} status`} />
        </Group>
        <AsOf at={scan.finished_at ? Date.parse(scan.finished_at) : null} prefix="Scanned" />
        <Text size="xs" c="dimmed">
          Started {fmt(scan.started_at)} ·{" "}
          {scan.finished_at
            ? `finished ${fmt(scan.finished_at)}`
            : scan.status === "FAILED"
              ? "aborted"
              : "in progress"}{" "}
          · by {scan.triggered_by}
        </Text>
        <Table withRowBorders={false} verticalSpacing={2} aria-label={`${title} scan counts`}>
          <Table.Tbody>
            {Object.entries(scan.counts)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, v]) => (
                <Table.Tr key={k}>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {humanizeToken(k)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs">{String(v)}</Text>
                  </Table.Td>
                </Table.Tr>
              ))}
          </Table.Tbody>
        </Table>
      </Stack>
    </Card>
  );
}

export function DriftStatusPage() {
  const { data, isLoading, isError, forbidden, refetch } = useDriftStatus();

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Drift status
        </Title>
        <NoAccessState
          message={
            <>
              You don&rsquo;t have access to the drift status surface. It requires the drift.read
              permission (System Administrator).
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading drift status" />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Drift status
        </Title>
        <ErrorState title="Couldn't load the drift status" onRetry={() => refetch()} />
      </Container>
    );
  }

  const cov = data.blob_coverage;
  const d4 = data.superseded_copies;
  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Title order={2}>Drift status</Title>
          <Text c="dimmed" size="sm">
            The vault&rsquo;s integrity detection legs: blob re-hash, mirror tamper/staleness, and
            outstanding superseded copies. The vault is the source of truth — these are detection
            reads, not corrections.
          </Text>
        </div>
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <ScanCard title="Mirror scan" scan={data.scans.MIRROR} />
          <ScanCard title="Blob integrity" scan={data.scans.BLOB_REHASH} />
        </SimpleGrid>
        <Card withBorder>
          <Stack gap="xs">
            <Text fw={600}>Blob verification coverage</Text>
            {cov.failing > 0 && (
              <Alert color="red" title="Integrity findings open">
                {cov.failing} unresolved integrity findings — re-alarming until restored. See the
                runbook (restore from backup, then re-run the verify).
              </Alert>
            )}
            <Group gap="lg">
              <Text size="sm">Total blobs: {cov.total}</Text>
              <Text size="sm">Never verified: {cov.never_verified}</Text>
              <Text size="sm">Failing: {cov.failing}</Text>
              <Text size="sm" c="dimmed">
                Oldest stamp: {cov.oldest_verified_at ? fmt(cov.oldest_verified_at) : "—"}
              </Text>
            </Group>
          </Stack>
        </Card>
        <Card withBorder>
          <Stack gap="xs">
            <Text fw={600}>Outstanding copies of superseded versions</Text>
            <Text size="sm">
              {d4.versions} versions · {d4.copies} exported/printed copies still in circulation.
            </Text>
            <Anchor component={Link} to="/drift/superseded-copies" size="sm">
              View the report →
            </Anchor>
          </Stack>
        </Card>
      </Stack>
    </Container>
  );
}
