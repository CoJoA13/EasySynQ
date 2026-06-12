import { Anchor, Badge, Group, Paper, Skeleton, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { coverageRag, RAG_META } from "./rag";

// The header health summary: the ★ mandatory-clause coverage as a single status (N9 — status against a
// configured rule, never a "you are compliant" verdict). The band drills to /compliance.
export function HealthSummary() {
  const { data, isLoading, isError, forbidden } = useComplianceChecklist();

  let body: ReactNode;
  if (forbidden) {
    body = <Text size="sm" c="dimmed">Coverage scoped to your access.</Text>;
  } else if (isLoading) {
    body = <Skeleton height={20} width={240} />;
  } else if (isError || !data) {
    body = <Text size="sm" c="dimmed">Couldn&apos;t load coverage.</Text>;
  } else {
    const rag = coverageRag(data.rollup);
    body = (
      <Group gap="sm" align="center" wrap="wrap">
        <Text fw={500}>
          {data.rollup.covered} / {data.rollup.total} mandatory items current
        </Text>
        <Badge
          variant="light"
          color={RAG_META[rag].color}
          leftSection={<span aria-hidden>{RAG_META[rag].glyph}</span>}
          aria-label={`Coverage status: ${RAG_META[rag].label}`}
        >
          {RAG_META[rag].label}
        </Badge>
        <Text size="xs" c="dimmed">
          status against configured thresholds — not a compliance verdict
        </Text>
      </Group>
    );
  }

  // Anchor(component={Link}) is the codebase's established polymorphic-link idiom (QuadrantCard/TopBar);
  // it wraps the Paper so the whole band is the /compliance drill-through with one discernible name.
  return (
    <Anchor
      component={Link}
      to="/compliance"
      underline="never"
      aria-label="QMS coverage summary; open the compliance checklist"
      style={{ display: "block", color: "inherit" }}
    >
      <Paper withBorder radius="md" p="md">
        {body}
      </Paper>
    </Anchor>
  );
}
