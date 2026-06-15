import { Alert, Button, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { ConfirmDestructive } from "../../lib/ConfirmDestructive";
import type { Audit } from "../../lib/types";
import { AUDIT_STATE_LABEL, AUDIT_STATE_ORDER, NEXT_TRANSITION } from "./labels";
import { useAdvanceAudit } from "./mutations";

// #3: the two irreversible audit transitions get a confirm. The others (plan/conduct/draft/begin-closing)
// are benign forward steps that fire directly.
const AUDIT_CONFIRM: Record<
  string,
  { title: string; consequence: string; confirmLabel: string; confirmColor: string }
> = {
  report: {
    title: "Issue the findings report?",
    consequence: "Publishes the findings as the official audit record.",
    confirmLabel: "Issue the report",
    confirmColor: "teal",
  },
  close: {
    title: "Close this audit?",
    consequence: "Closes the audit.",
    confirmLabel: "Close the audit",
    confirmColor: "red",
  },
};

// The 7-node lifecycle stepper + the ONE legal next transition (the backend FSM is linear).
// Gate = NEXT_TRANSITION[state].gate (audit.conduct → audit.close at the close phase), asked at
// the audit's PROCESS scope (SYSTEM fallback) — the 7b AdvancePanel shape. The server is the
// authority: 409s (invalid_audit_transition / audit_close_blocked) render calmly inline.
export function AuditLifecyclePanel({
  audit,
  scope,
}: {
  audit: Audit;
  scope: { level: string; id?: string };
}) {
  const perms = usePermissions(scope);
  const advance = useAdvanceAudit(audit.id);
  const next = NEXT_TRANSITION[audit.state];
  const currentIdx = AUDIT_STATE_ORDER.indexOf(audit.state);
  const [confirming, setConfirming] = useState(false);
  const confirmCfg = next ? AUDIT_CONFIRM[next.path] : undefined;

  return (
    <Paper withBorder p="md">
      <Title order={5} mb="sm">
        Lifecycle
      </Title>
      <Stack gap={4} mb="md">
        {AUDIT_STATE_ORDER.map((s, i) => {
          const glyph = i < currentIdx ? "✓" : i === currentIdx ? "●" : "○";
          return (
            <div key={s} aria-current={i === currentIdx ? "step" : undefined}>
              <Text
                size="sm"
                fw={i === currentIdx ? 700 : 400}
                c={i > currentIdx ? "dimmed" : undefined}
              >
                {glyph} {AUDIT_STATE_LABEL[s]}
              </Text>
            </div>
          );
        })}
      </Stack>
      {next === null ? (
        <Text size="sm" c="dimmed">
          Audit closed{audit.completed_at ? ` on ${audit.completed_at}` : ""}.
        </Text>
      ) : perms.isLoading ? (
        <Loader size="sm" />
      ) : !perms.can(next.gate) ? (
        <Text size="sm" c="dimmed">
          You don't hold the permission to advance this audit.
        </Text>
      ) : (
        <Stack gap="sm">
          {/* For the confirmed transitions (report/close) the error surfaces INSIDE the dialog — this
              inline Alert is only for the direct (non-confirmed) forward steps, so it can't double up. */}
          {!confirmCfg && advance.isError && (
            <Alert
              color="orange"
              title={
                advance.error instanceof ApiError && advance.error.code === "audit_close_blocked"
                  ? "Close blocked"
                  : "Couldn't advance"
              }
            >
              {advance.error instanceof ApiError ? advance.error.message : "Please try again."}
            </Alert>
          )}
          <Button
            onClick={() => (confirmCfg ? setConfirming(true) : advance.mutate(next.path))}
            loading={advance.isPending}
          >
            {next.label}
          </Button>
          {confirmCfg && (
            <ConfirmDestructive
              opened={confirming}
              onCancel={() => setConfirming(false)}
              onConfirm={async () => {
                await advance.mutateAsync(next.path);
                setConfirming(false);
              }}
              title={confirmCfg.title}
              consequence={confirmCfg.consequence}
              confirmLabel={confirmCfg.confirmLabel}
              confirmColor={confirmCfg.confirmColor}
              mapError={(e) => (e instanceof ApiError ? e.message : "Please try again.")}
            />
          )}
        </Stack>
      )}
    </Paper>
  );
}
