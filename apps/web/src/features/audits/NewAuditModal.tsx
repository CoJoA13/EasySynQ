import { Alert, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { useAuditPlans, useAuditPrograms } from "./hooks";
import { useCreateAudit } from "./mutations";

// POST /audits needs a plan_id — the cascade picks programme → that programme's plans. The lead
// auditor defaults server-side to the plan's lead; the optional picker rides the user directory
// (degrades to absent when the directory is empty/denied).
export function NewAuditModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const programs = useAuditPrograms();
  const [programId, setProgramId] = useState<string | null>(null);
  const plans = useAuditPlans(programId);
  const { data: directory } = useUserDirectory();
  const [planId, setPlanId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [leadId, setLeadId] = useState<string | null>(null);
  const create = useCreateAudit();

  const programRows = programs.data ?? [];
  const planRows = plans.data ?? [];
  const directoryRows = directory ?? [];

  function submit() {
    if (!planId) return;
    create.mutate(
      {
        plan_id: planId,
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(leadId ? { lead_auditor_user_id: leadId } : {}),
      },
      { onSuccess: (audit) => navigate(`/audits/${audit.id}`) },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New audit">
      {!programs.isLoading && programRows.length === 0 ? (
        <Text c="dimmed">
          No audit plans yet — create a programme and add a plan on the Programme tab first.
        </Text>
      ) : (
        <Stack gap="sm">
          <Select
            label="Programme"
            placeholder="Pick a programme"
            data={programRows.map((p) => ({ value: p.id, label: `${p.identifier} — ${p.title}` }))}
            value={programId}
            onChange={(v) => {
              setProgramId(v);
              setPlanId(null);
            }}
          />
          <Select
            label="Plan"
            placeholder={programId ? "Pick a plan" : "Pick a programme first"}
            disabled={!programId}
            data={planRows.map((p) => ({
              value: p.id,
              label: [p.scheduled_date ?? "unscheduled", p.checklist_ref].filter((x): x is string => Boolean(x)).join(" · "),
            }))}
            value={planId}
            onChange={setPlanId}
          />
          <TextInput
            label="Title (optional)"
            value={title}
            onChange={(e) => setTitle(e.currentTarget.value)}
          />
          {directoryRows.length > 0 && (
            <Select
              label="Lead auditor (optional — defaults to the plan's)"
              data={directoryRows.map((u) => ({ value: u.id, label: u.display_name ?? u.id }))}
              value={leadId}
              onChange={setLeadId}
              clearable
            />
          )}
          {create.isError && (
            <Alert color="red" title="Couldn't create the audit">
              {create.error instanceof ApiError ? create.error.message : "Please try again."}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!planId} loading={create.isPending}>
              Create audit
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
