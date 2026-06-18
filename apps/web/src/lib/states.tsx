import { Alert, Button, Center, Loader, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

// The shared calm state primitives for the whole SPA — one place for "loading", "couldn't load",
// "no access", and "nothing here yet". Before this they were hand-rolled per feature (a bare <Loader/>
// with no accessible name, a red <Alert> with NO retry affordance, a gray "No access" Alert, a dimmed
// <Text>). These are deliberately INNER / layout-agnostic: a caller keeps its own <Container>/<Card>/
// <Title> wrapper and drops one of these in where the ad-hoc panel was, so the sweep changes the panel
// body, never the page chrome. Status here is calm, never alarming — a register that 403s reads as a
// quiet "no access", not an error.

// A centered spinner with an accessible name (the old bare <Loader/> announced nothing to AT). `label`
// is the accessible name; `mih` keeps the spinner vertically settled inside its container.
export function LoadingState({ label = "Loading", mih = 120 }: { label?: string; mih?: number }) {
  // The accessible name + live-region role live on the wrapper, not the <Loader> — Mantine renders the
  // loader as a roleless <span>, and aria-label on a roleless span is prohibited (axe aria-prohibited-attr).
  return (
    <Center mih={mih} role="status" aria-label={label}>
      <Loader />
    </Center>
  );
}

// A calm "couldn't load" panel. The headline missing affordance it adds over the hand-rolled panels is
// `onRetry` → a "Try again" button (wire it to the query's refetch). The default copy matches the
// long-standing "Please try again." so existing assertions survive the sweep; a caller passes a
// surface-specific `title` ("Couldn't load change requests").
export function ErrorState({
  title = "Couldn't load this",
  message = "Please try again.",
  onRetry,
}: {
  title?: string;
  message?: ReactNode;
  onRetry?: () => void;
}) {
  return (
    <Alert color="red" title={title}>
      <Stack gap="sm" align="flex-start">
        <Text size="sm">{message}</Text>
        {onRetry && (
          <Button variant="light" color="red" size="compact-sm" onClick={onRetry}>
            Try again
          </Button>
        )}
      </Stack>
    </Alert>
  );
}

// A calm no-access panel (the deny-by-default surface a caller hits when a read 403s). Gray, titled
// "No access" verbatim (the title every existing test asserts on); `message` explains which permission
// gates the surface.
export function NoAccessState({ message }: { message: ReactNode }) {
  return (
    <Alert color="gray" title="No access">
      {message}
    </Alert>
  );
}

// A calm "nothing here yet" body — a dimmed message + an optional action (e.g. a "Raise the first one"
// button). Left-aligned and chrome-free so it drops into the place a hand-rolled `<Text c="dimmed">No X
// yet.</Text>` used to sit; the caller's surrounding spacing is preserved.
export function EmptyState({ message, action }: { message: ReactNode; action?: ReactNode }) {
  // Default (body) size — matches the long-standing `<Text c="dimmed">No X yet.</Text>` empties so
  // adoption never shrinks an existing empty (preserve-the-look). An optional action sits below.
  if (!action) {
    return <Text c="dimmed">{message}</Text>;
  }
  return (
    <Stack gap="sm" align="flex-start">
      <Text c="dimmed">{message}</Text>
      {action}
    </Stack>
  );
}
