import { Button, Center, Image, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import type { AuthFailureKind, AuthStatus } from "../../lib/auth";

type StartupStatus = Exclude<AuthStatus, { kind: "ready" }>;

export interface AuthStartupScreenProps {
  status: StartupStatus;
  onRetry: () => Promise<void>;
  onReload: () => void;
}

const FAILURE_COPY: Record<AuthFailureKind, { heading: string; guidance: string }> = {
  configuration: {
    heading: "Sign-in is unavailable",
    guidance: "EasySynQ could not connect to its sign-in service.",
  },
  callback: {
    heading: "Sign-in was not completed",
    guidance: "Your sign-in response could not be verified.",
  },
  session: {
    heading: "Your session could not be loaded",
    guidance: "EasySynQ could not restore your sign-in session.",
  },
  redirect: {
    heading: "Sign-in could not be opened",
    guidance: "EasySynQ could not open the sign-in page.",
  },
  timeout: {
    heading: "Sign-in is taking too long",
    guidance: "The sign-in service did not respond in time.",
  },
};

export function AuthStartupScreen({ status, onRetry, onReload }: AuthStartupScreenProps) {
  const [retryBusy, setRetryBusy] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusedFailureRef = useRef<AuthFailureKind | null>(null);
  const failureKind = status.kind === "error" ? status.failure.kind : null;

  useEffect(() => {
    if (!failureKind) {
      focusedFailureRef.current = null;
      return;
    }
    if (focusedFailureRef.current !== failureKind) {
      headingRef.current?.focus();
      focusedFailureRef.current = failureKind;
    }
  }, [failureKind]);

  const handleRetry = async () => {
    if (retryBusy) return;
    setRetryBusy(true);
    try {
      await onRetry();
    } catch {
      // Provider failures are represented by the next auth status, never an unhandled view promise.
    } finally {
      setRetryBusy(false);
    }
  };

  const content =
    status.kind === "loading" ? (
      <Stack
        align="center"
        gap="sm"
        role="status"
        aria-live="polite"
        aria-label="Connecting to sign-in"
      >
        <Loader aria-hidden="true" />
        <Text fw={600}>Connecting to sign-in…</Text>
        <Text c="var(--es-text-2)" size="sm" ta="center">
          Please wait while we securely connect you.
        </Text>
      </Stack>
    ) : (
      <Stack align="stretch" gap="lg" aria-live="polite">
        <Stack align="center" gap="sm">
          <Title ref={headingRef} order={1} size="h2" ta="center" tabIndex={-1}>
            {FAILURE_COPY[status.failure.kind].heading}
          </Title>
          <Text c="var(--es-text-2)" ta="center">
            {FAILURE_COPY[status.failure.kind].guidance}
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button
            fullWidth
            loading={retryBusy}
            disabled={retryBusy}
            aria-busy={retryBusy || undefined}
            style={{ minHeight: 44 }}
            onClick={handleRetry}
          >
            Try sign-in again
          </Button>
          <Button
            fullWidth
            variant="subtle"
            color="gray"
            style={{ minHeight: 44 }}
            onClick={onReload}
          >
            Reload EasySynQ
          </Button>
        </Stack>
        <Text c="var(--es-text-2)" size="sm" ta="center">
          If this keeps happening, contact your EasySynQ administrator.
        </Text>
      </Stack>
    );

  return (
    <Center mih="100dvh" px="lg" py="lg" bg="var(--es-bg)">
      <Paper
        component="main"
        w="100%"
        maw={440}
        miw={0}
        p={{ base: "xl", sm: 48 }}
        radius="md"
        withBorder
        shadow="xs"
        bg="var(--es-surface)"
      >
        <Stack align="center" gap="xl">
          <Image src="/easysynq-mark.svg" alt="EasySynQ" w={64} h={64} fit="contain" />
          {content}
        </Stack>
      </Paper>
    </Center>
  );
}
