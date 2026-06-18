import {
  Alert,
  Anchor,
  Breadcrumbs,
  Container,
  Grid,
  Group,
  Paper,
  Text,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { LoadingState, NoAccessState } from "../../lib/states";
import type { Finding } from "../../lib/types";
import { AuditStateBadge } from "./badges";
import { CorrectFindingModal } from "./CorrectFindingModal";
import { FindingsCard } from "./FindingsCard";
import { useAudit, useAuditPlan, useAuditPrograms, useProcesses } from "./hooks";
import { AuditLifecyclePanel } from "./AuditLifecyclePanel";
import { LogFindingModal } from "./LogFindingModal";

// The /audits/:id destination (outside the tab layout — the documents/:id precedent). Hosts the
// plan/programme context card + (Tasks 13/14) the lifecycle panel and the findings card. The FSM
// write scope is the plan's auditee process (SYSTEM fallback) — resolved HERE and passed down.
export function AuditDetailPage() {
  const { id } = useParams<{ id: string }>();
  const audit = useAudit(id ?? null);
  const plan = useAuditPlan(audit.data?.plan_id ?? null);
  const programs = useAuditPrograms(); // cached list — programme title lookup, no extra endpoint
  const processes = useProcesses();
  const { data: directory } = useUserDirectory();
  const [logOpen, setLogOpen] = useState(false);
  const [correcting, setCorrecting] = useState<Finding | null>(null);

  if (audit.forbidden) {
    return (
      <Container size="xl" py="md">
        <NoAccessState
          message={
            <>
              You don't have access to internal audits. They're available to roles holding{" "}
              <code>audit.read</code>.
            </>
          }
        />
      </Container>
    );
  }
  if (audit.isLoading) {
    return (
      <Container size="xl" py="md">
        <LoadingState label="Loading audit" />
      </Container>
    );
  }
  if (audit.isError || !audit.data) {
    return (
      <Container size="xl" py="md">
        <Alert color="gray" title="Audit not found">
          This audit doesn't exist or was removed.{" "}
          <Anchor component={Link} to="/audits">
            Back to audits
          </Anchor>
        </Alert>
      </Container>
    );
  }

  const a = audit.data;
  const p = plan.data ?? null;
  const programTitle = p
    ? ((programs.data ?? []).find((x) => x.id === p.program_id)?.title ?? null)
    : null;
  const processName = p?.auditee_process_id
    ? ((processes.data ?? []).find((x) => x.id === p.auditee_process_id)?.name ??
      `${p.auditee_process_id.slice(0, 8)}…`)
    : null;
  const lead = a.lead_auditor_user_id
    ? ((directory ?? []).find((u) => u.id === a.lead_auditor_user_id)?.display_name ??
      `${a.lead_auditor_user_id.slice(0, 8)}…`)
    : "—";
  // The FSM/finding write scope (the _audit_scope mirror): PROCESS when the auditee is set, else SYSTEM.
  // Tasks 13/14 consume scope — declared here so the whole page owns it.
  const scope: { level: string; id?: string } = p?.auditee_process_id
    ? { level: "PROCESS", id: p.auditee_process_id }
    : { level: "SYSTEM" };

  return (
    <Container size="xl" py="md">
      <Breadcrumbs mb="sm">
        <Anchor component={Link} to="/audits">
          Internal Audit
        </Anchor>
        <Text>{a.identifier ?? a.id.slice(0, 8)}</Text>
      </Breadcrumbs>
      <Group justify="space-between" mb="md" align="flex-start">
        <div>
          <Title order={3}>{a.title ?? "Internal audit"}</Title>
          <Text size="sm" c="dimmed">
            Lead auditor{" "}
            <Text span fw={500}>
              {lead}
            </Text>
            {a.started_at ? ` · started ${a.started_at}` : ""}
            {a.completed_at ? ` · completed ${a.completed_at}` : ""}
          </Text>
        </div>
        <AuditStateBadge state={a.state} />
      </Group>
      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <FindingsCard
            audit={a}
            scope={scope}
            onLog={() => setLogOpen(true)}
            onCorrect={setCorrecting}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper withBorder p="md" mb="md">
            <Title order={4} mb="xs">
              Plan
            </Title>
            {p ? (
              <Text size="sm">
                {programTitle ? `${programTitle} · ` : ""}
                {p.scheduled_date ?? "unscheduled"}
                {p.checklist_ref ? ` · ${p.checklist_ref}` : ""}
                {processName ? ` · Auditee process ${processName}` : ""}
              </Text>
            ) : (
              <Text size="sm" c="dimmed">
                Plan unavailable.
              </Text>
            )}
          </Paper>
          <AuditLifecyclePanel audit={a} scope={scope} />
        </Grid.Col>
      </Grid>
      {/* Conditionally rendered so close UNMOUNTS it — its post-NC confirmation state must not
          survive a reopen (the CorrectFindingModal / ProgramForm keyed-remount precedent). */}
      {logOpen && <LogFindingModal auditId={a.id} opened onClose={() => setLogOpen(false)} />}
      {correcting && (
        <CorrectFindingModal
          key={correcting.id}
          finding={correcting}
          auditId={a.id}
          opened
          onClose={() => setCorrecting(null)}
        />
      )}
    </Container>
  );
}
