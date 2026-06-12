import { Alert, Anchor, Badge, Button, Container, Group, Loader, SegmentedControl, Table, Text, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { NewObjectiveModal } from "./NewObjectiveModal";
import type { Objective, ObjectiveRag } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveScorecard } from "./hooks";
import { fmtValueUnit, RAG_COLOR, RAG_LABEL } from "./labels";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";
import { StateBadge } from "../document/StateBadge";

function currentOverTarget(o: Objective): string {
  return `${fmtValueUnit(o.current_value, "").trim() || "—"} / ${o.target_value} ${o.unit}`.trim();
}

export function ObjectivesRegisterPage() {
  const { data, isLoading, isError, forbidden } = useObjectiveScorecard();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [rag, setRag] = useState<ObjectiveRag | "">("");
  const [createOpen, setCreateOpen] = useState(false);

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

  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Quality objectives</Title>
        <Alert color="red" title="Couldn't load quality objectives">
          Something went wrong loading the objectives. Please try again.
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
        {can("objective.manage") && (
          <Button onClick={() => setCreateOpen(true)}>New objective</Button>
        )}
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
                  <Group gap="xs" wrap="nowrap">
                    <Anchor component={Link} to={`/objectives/${o.id}`}>
                      {o.identifier}
                    </Anchor>
                    {/* O-6c: exception-marking — the steady state (Effective) stays unmarked;
                        Draft/InReview/UnderRevision/... get the shared StateBadge. */}
                    {o.current_state !== "Effective" && (
                      <StateBadge state={o.current_state} size="xs" />
                    )}
                  </Group>
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
      {createOpen && (
        <NewObjectiveModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false);
            navigate(`/objectives/${id}`);
          }}
        />
      )}
    </Container>
  );
}
