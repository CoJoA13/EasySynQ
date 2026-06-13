import { Alert, Anchor, Button, Container, Group, Loader, Select, Table, Text, Title } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DcrChangeType, DcrReasonClass, DcrState } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { DcrDrawer } from "./DcrDrawer";
import { DcrStateBadge } from "./DcrStateBadge";
import { CHANGE_TYPE_LABEL, REASON_LABEL } from "./labels";
import { useDcrs } from "./hooks";
import { RaiseDcrModal } from "./RaiseDcrModal";

const STATES: DcrState[] = [
  "Open",
  "Assessed",
  "Routed",
  "InApproval",
  "Approved",
  "Implemented",
  "Closed",
  "Cancelled",
  "Rejected",
];

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function DcrsRegisterPage() {
  const { data, isLoading, isError, forbidden } = useDcrs();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(() => params.get("dcr"));
  const [state, setState] = useState<DcrState | "">("");
  const [changeType, setChangeType] = useState<DcrChangeType | "">("");
  const [reason, setReason] = useState<DcrReasonClass | "">("");
  const { can } = usePermissions();
  const [raising, setRaising] = useState(false);

  // Open the drawer for ?dcr=<id> on mount + whenever the param changes (a deep-link while mounted).
  // Guarded on a non-null id so clearing the param on close never re-opens the drawer.
  useEffect(() => {
    const dcr = params.get("dcr");
    if (dcr) setSelected(dcr);
  }, [params]);

  function closeDrawer() {
    setSelected(null);
    // Only touch the URL when a deep-link param is actually present, so the common (local) open/close
    // path leaves history untouched. Replace, so closing doesn't leave a back-step that re-opens it.
    if (params.has("dcr")) {
      setParams(
        (p) => {
          p.delete("dcr");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = data ?? [];
  const filtered = useMemo(
    () =>
      rows.filter(
        (d) =>
          (state === "" || d.state === state) &&
          (changeType === "" || d.change_type === changeType) &&
          (reason === "" || d.reason_class === reason),
      ),
    [rows, state, changeType, reason],
  );

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the change-request register. It's available to roles holding the
          change-request read permission.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="red" title="Couldn't load change requests">
          Please try again.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Change requests</Title>
        {can("changeRequest.create") && (
          <Button onClick={() => setRaising(true)}>Raise DCR</Button>
        )}
      </Group>
      <Group mb="md" gap="sm">
        <Select
          aria-label="State"
          placeholder="All states"
          clearable
          value={state || null}
          onChange={(v) => setState((v as DcrState) ?? "")}
          data={STATES.map((s) => ({ value: s, label: s }))}
        />
        <Select
          aria-label="Change type"
          placeholder="All change types"
          clearable
          value={changeType || null}
          onChange={(v) => setChangeType((v as DcrChangeType) ?? "")}
          data={(Object.entries(CHANGE_TYPE_LABEL) as [DcrChangeType, string][]).map(
            ([value, label]) => ({ value, label }),
          )}
        />
        <Select
          aria-label="Reason"
          placeholder="All reasons"
          clearable
          value={reason || null}
          onChange={(v) => setReason((v as DcrReasonClass) ?? "")}
          data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(
            ([value, label]) => ({ value, label }),
          )}
        />
      </Group>

      {filtered.length === 0 ? (
        <Text c="dimmed">No change requests yet.</Text>
      ) : (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Significance</Table.Th>
              <Table.Th>Reason</Table.Th>
              <Table.Th>Target</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Created</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td>
                  <Anchor component="button" type="button" onClick={() => setSelected(d.id)}>
                    {d.identifier}
                  </Anchor>
                </Table.Td>
                <Table.Td>{CHANGE_TYPE_LABEL[d.change_type]}</Table.Td>
                <Table.Td>{d.change_significance}</Table.Td>
                <Table.Td>{REASON_LABEL[d.reason_class]}</Table.Td>
                <Table.Td>{d.target_document_id ? "Document" : "—"}</Table.Td>
                <Table.Td>
                  <DcrStateBadge state={d.state} />
                </Table.Td>
                <Table.Td>{formatDate(d.created_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <DcrDrawer dcrId={selected} onClose={closeDrawer} />
      {raising && (
        <RaiseDcrModal
          onClose={() => setRaising(false)}
          onCreated={(id) => setSelected(id)}
        />
      )}
    </Container>
  );
}
