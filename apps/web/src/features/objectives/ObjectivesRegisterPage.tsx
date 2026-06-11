import { Alert, Anchor, Badge, Container, Group, Loader, SegmentedControl, Table, Text, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Objective, ObjectiveRag } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveScorecard } from "./hooks";
import { fmtValueUnit, RAG_COLOR, RAG_LABEL } from "./labels";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";

function currentOverTarget(o: Objective): string {
  return `${fmtValueUnit(o.current_value, "").trim() || "—"} / ${o.target_value} ${o.unit}`.trim();
}

export function ObjectivesRegisterPage() {
  const { data, isLoading, forbidden } = useObjectiveScorecard();
  const { can } = usePermissions();
  const [rag, setRag] = useState<ObjectiveRag | "">("");

  const rows = useMemo(
    () => (data?.objectives ?? []).filter((o) => rag === "" || o.rag === rag),
    [data, rag],
  );

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Quality objectives</Title>
        <Alert color="gray" title="No access">
          You don't have access to Quality Objectives. It's available to the Quality Manager and
          Process Owner roles.
        </Alert>
      </Container>
    );
  }

  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Quality objectives</Title>
        {/* The New-objective button (gated objective.manage) is wired in Task 12. */}
      </Group>

      <ObjectiveScorecardBand total={data.total} onTarget={data.on_target} byRag={data.by_rag} />

      <SegmentedControl
        mt="md"
        value={rag}
        onChange={(v) => setRag(v as ObjectiveRag | "")}
        aria-label="Filter by RAG status"
        data={[
          { value: "", label: "All" },
          { value: "green", label: RAG_LABEL.green },
          { value: "amber", label: RAG_LABEL.amber },
          { value: "red", label: RAG_LABEL.red },
          { value: "unmeasured", label: RAG_LABEL.unmeasured },
        ]}
      />

      {data.objectives.length === 0 ? (
        <Alert color="gray" title="No quality objectives yet" mt="md">
          {can("objective.manage")
            ? "Create the first objective to start tracking progress against target."
            : "No objectives have been set up yet."}
        </Alert>
      ) : (
        <Table striped highlightOnHover mt="md">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Ref</Table.Th>
              <Table.Th>Objective</Table.Th>
              <Table.Th>Current / target</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Due</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((o) => (
              <Table.Tr key={o.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/objectives/${o.id}`}>
                    {o.identifier}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{o.title}</Text>
                </Table.Td>
                <Table.Td>{currentOverTarget(o)}</Table.Td>
                <Table.Td>
                  <Badge color={RAG_COLOR[o.rag]} variant="light">
                    {RAG_LABEL[o.rag]}
                  </Badge>
                </Table.Td>
                <Table.Td>{o.due_date}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Container>
  );
}
