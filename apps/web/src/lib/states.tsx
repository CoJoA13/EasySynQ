import { Alert, Anchor, Button, Center, Loader, Skeleton, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { ApiError } from "./api";

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

// The COMPACT inline-state idiom — a sibling of the four page-level primitives above, sized for an
// in-tab / in-drawer / in-card body where a full <Alert> panel or a centered <Loader> is too heavy. It
// renders ONE `<Text size="sm">`, matching the long-standing hand-rolled `<Text size="sm" c="dimmed">` /
// `<Text size="sm" c="red">` it consolidates across the document tabs, the /tasks + /acks inboxes, and
// the DCR diff/impact surfaces. The `kind` carries both the semantics and the colour: a calm dimmed
// read for loading / forbidden / empty, red for a genuine load failure. `loading` is a polite live
// region (role="status") so AT announces it when a tab body swaps in; the others are static text.
// `onRetry` (error only) adds the inline "Try again" affordance the hand-rolled inline errors lacked —
// the same gap ErrorState closed for the page-level panels — wired to the query's refetch.
export function InlineState({
  kind,
  children,
  onRetry,
}: {
  kind: "loading" | "forbidden" | "empty" | "error";
  children: ReactNode;
  onRetry?: () => void;
}) {
  if (kind === "error") {
    return (
      <Text size="sm" c="red">
        {children}
        {onRetry && (
          <>
            {" "}
            <Anchor component="button" type="button" onClick={onRetry} c="red">
              Try again
            </Anchor>
          </>
        )}
      </Text>
    );
  }
  // loading announces (live region); forbidden/empty are calm static text. role="status" on the <Text>
  // (a <p>) is valid and lets the dimmed message carry its own accessible name.
  return (
    <Text size="sm" c="dimmed" {...(kind === "loading" ? { role: "status" } : {})}>
      {children}
    </Text>
  );
}

// A list/table loading placeholder — N stacked <Skeleton> rows. Replaces the hand-rolled
// `<Stack gap="xs">{…map(<Skeleton/>)}</Stack>` table loaders (LibraryPage / TriageTable /
// IngestionRunsPage) with one configurable primitive; `rows`/`height` mirror each table's row count +
// row height. It is always a live region (role="status", overridable) so a screen reader hears the
// surface is loading; `label` supplies the accessible name (an aria-label on a roleless container is an
// axe violation — hanging it off role="status" keeps it clean, and fixes the prior nameless loaders).
export function SkeletonList({
  rows,
  height,
  gap = "xs",
  label,
  role = "status",
}: {
  rows: number;
  height: number;
  gap?: number | string;
  label?: string;
  role?: string;
}) {
  return (
    <Stack gap={gap} role={role} aria-label={label}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={height} />
      ))}
    </Stack>
  );
}

// A calm mutation-error panel — the write-side sibling of ErrorState. The create/update forms that each
// hand-rolled `<Alert color="red" title="…">{e instanceof ApiError ? e.message : "Please try again."}
// </Alert>` on a failed mutation now share one component: it unwraps the RFC-9457 ApiError message
// centrally and renders the calm red Alert. `error` is the mutation's `.error` (unknown); `fallback` is
// shown when it isn't an ApiError. Distinct from ErrorState (a READ failure, with a retry button) — a
// mutation error surfaces the server's reason and is retried by re-submitting the form.
export function MutationErrorState({
  title,
  error,
  fallback = "Please try again.",
}: {
  title: string;
  error: unknown;
  fallback?: ReactNode;
}) {
  return (
    <Alert color="red" title={title}>
      {error instanceof ApiError ? error.message : fallback}
    </Alert>
  );
}
