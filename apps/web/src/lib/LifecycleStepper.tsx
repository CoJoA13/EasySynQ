import { Box, Stack, Text, VisuallyHidden } from "@mantine/core";
import { TONE_GLYPH } from "./status";

export type LifecycleStepStatus = "done" | "current" | "pending" | "rejected";

export interface LifecycleStep {
  key: string;
  label: string;
  description?: string;
  status: LifecycleStepStatus;
}

const MARK: Record<LifecycleStepStatus, string> = {
  done: TONE_GLYPH.success,
  current: TONE_GLYPH.info,
  pending: TONE_GLYPH.neutral,
  rejected: TONE_GLYPH.danger,
};

const STATUS_LABEL: Record<LifecycleStepStatus, string> = {
  done: "Completed",
  current: "Current",
  pending: "Pending",
  rejected: "Rejected",
};

const MARK_STYLE: Record<
  LifecycleStepStatus,
  { background: string; border: string; color: string }
> = {
  done: {
    background: "var(--es-success)",
    border: "1px solid transparent",
    color: "var(--es-text-inverse)",
  },
  current: {
    background: "var(--es-info)",
    border: "1px solid transparent",
    color: "var(--es-text-inverse)",
  },
  pending: {
    background: "var(--es-surface-2)",
    border: "1px solid var(--es-border-strong)",
    color: "var(--es-text-2)",
  },
  rejected: {
    background: "var(--es-danger)",
    border: "1px solid transparent",
    color: "var(--es-text-inverse)",
  },
};

// One progress/checklist treatment for workflow lifecycles throughout the SPA. The marker always
// carries a canonical non-colour glyph, and every foreground/background pair re-keys through the
// semantic theme tokens in dark mode. Domain-specific copy stays with the caller.
export function LifecycleStepper({
  ariaLabel,
  steps,
}: {
  ariaLabel: string;
  steps: LifecycleStep[];
}) {
  return (
    <Stack
      gap={0}
      component="ol"
      aria-label={ariaLabel}
      style={{ listStyle: "none", padding: 0, margin: 0 }}
    >
      {steps.map((step, index) => (
        <Box
          component="li"
          key={step.key}
          data-lifecycle-status={step.status}
          aria-current={step.status === "current" ? "step" : undefined}
          style={{
            display: "flex",
            gap: 12,
            paddingBottom: index < steps.length - 1 ? 16 : 0,
          }}
        >
          <Box
            aria-hidden="true"
            data-lifecycle-marker={step.status}
            style={{
              width: 22,
              height: 22,
              borderRadius: "var(--es-radius-circle)",
              boxSizing: "border-box",
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              ...MARK_STYLE[step.status],
            }}
          >
            {MARK[step.status]}
          </Box>
          <Box>
            <VisuallyHidden>Status: {STATUS_LABEL[step.status]}</VisuallyHidden>
            <Text fw={step.status === "current" ? 700 : 600} size="sm">
              {step.label}
            </Text>
            {step.description && (
              <Text size="xs" c="dimmed">
                {step.description}
              </Text>
            )}
          </Box>
        </Box>
      ))}
    </Stack>
  );
}
