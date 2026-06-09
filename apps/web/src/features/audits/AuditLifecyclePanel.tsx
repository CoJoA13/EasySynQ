import { Alert, Button, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import type { Audit } from "../../lib/types";
import { AUDIT_STATE_LABEL, AUDIT_STATE_ORDER, NEXT_TRANSITION } from "./labels";
import { useAdvanceAudit } from "./mutations";

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
              <Text size="sm" fw={i === currentIdx ? 700 : 400} c={i > currentIdx ? "dimmed" : undefined}>
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
          {advance.isError && (
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
          <Button onClick={() => advance.mutate(next.path)} loading={advance.isPending}>
            {next.label}
          </Button>
        </Stack>
      )}
    </Paper>
  );
}
