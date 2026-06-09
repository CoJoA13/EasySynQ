import { Alert, Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { useProcesses } from "./hooks";
import { useCreatePlan } from "./mutations";

// Add a plan to a programme. The process picker rides process.read (omitted on 403 — degrade);
// the lead picker rides the user directory (omitted when empty). Date = plain YYYY-MM-DD input.
export function PlanForm({
  programId,
  opened,
  onClose,
}: {
  programId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [date, setDate] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);
  const [leadId, setLeadId] = useState<string | null>(null);
  const [checklistRef, setChecklistRef] = useState("");
  const processes = useProcesses();
  const { data: directory } = useUserDirectory();
  const create = useCreatePlan(programId);

  function submit() {
    create.mutate(
      {
        ...(date ? { scheduled_date: date } : {}),
        ...(processId ? { auditee_process_id: processId } : {}),
        ...(leadId ? { lead_auditor_user_id: leadId } : {}),
        ...(checklistRef.trim() ? { checklist_ref: checklistRef.trim() } : {}),
      },
      { onSuccess: onClose },
    );
  }

  const processRows = processes.forbidden ? [] : (processes.data ?? []);
  const directoryRows = directory ?? [];

  return (
    <Modal opened={opened} onClose={onClose} title="Add plan">
      <Stack gap="sm">
        <TextInput
          label="Scheduled date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.currentTarget.value)}
        />
        {processRows.length > 0 && (
          <Select
            label="Auditee process"
            data={processRows.map((p) => ({ value: p.id, label: p.name }))}
            value={processId}
            onChange={setProcessId}
            clearable
          />
        )}
        {directoryRows.length > 0 && (
          <Select
            label="Lead auditor"
            data={directoryRows.map((u) => ({ value: u.id, label: u.display_name ?? u.id }))}
            value={leadId}
            onChange={setLeadId}
            clearable
          />
        )}
        <TextInput
          label="Checklist ref"
          placeholder="e.g. FRM-AUD-002"
          value={checklistRef}
          onChange={(e) => setChecklistRef(e.currentTarget.value)}
        />
        {create.isError && (
          <Alert color="red" title="Couldn't save the plan">
            {create.error instanceof ApiError ? create.error.message : "Please try again."}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={create.isPending}>
            Save plan
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
