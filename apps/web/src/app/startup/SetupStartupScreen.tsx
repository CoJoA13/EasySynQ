import { Button, Center, Image, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

export type SetupStartupPhase = "initial" | "post-finalization";
export type SetupStartupStatus =
  { kind: "loading"; phase: SetupStartupPhase } | { kind: "error"; phase: SetupStartupPhase };

export interface SetupStartupScreenProps {
  status: SetupStartupStatus;
  onRetry: () => Promise<void>;
  onReload: () => void;
}

const COPY = {
  initial: {
    loading: {
      label: "Checking setup status",
      status: "Checking setup status…",
      guidance: "Please wait while EasySynQ verifies this installation.",
    },
    error: {
      heading: "Setup status is unavailable",
      guidance:
        "EasySynQ could not confirm whether this installation is ready. Setup changes are disabled until the status can be verified.",
    },
  },
  "post-finalization": {
    loading: {
      label: "Verifying setup",
      status: "Verifying setup…",
      guidance: "Setup was saved. EasySynQ is confirming that the installation is ready.",
    },
    error: {
      heading: "Setup was saved, but could not be verified",
      guidance: "Try checking the setup status again. EasySynQ will not repeat finalization.",
    },
  },
} as const;

export function SetupStartupScreen({ status, onRetry, onReload }: SetupStartupScreenProps) {
  const [retryBusy, setRetryBusy] = useState(false);
  const retryPromiseRef = useRef<Promise<void> | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusedPhaseRef = useRef<SetupStartupPhase | null>(null);

  useEffect(() => {
    if (status.kind !== "error") {
      focusedPhaseRef.current = null;
      return;
    }
    if (focusedPhaseRef.current !== status.phase) {
      headingRef.current?.focus();
      focusedPhaseRef.current = status.phase;
    }
  }, [status]);

  const handleRetry = async (): Promise<void> => {
    if (retryPromiseRef.current) return retryPromiseRef.current;
    setRetryBusy(true);
    const attempt = Promise.resolve().then(onRetry);
    retryPromiseRef.current = attempt;
    try {
      await attempt;
    } catch {
      // The next setup query state owns failure presentation.
    } finally {
      if (retryPromiseRef.current === attempt) retryPromiseRef.current = null;
      setRetryBusy(false);
    }
  };

  const phaseCopy = COPY[status.phase];
  const content =
    status.kind === "loading" ? (
      <Stack
        align="center"
        gap="sm"
        role="status"
        aria-live="polite"
        aria-label={phaseCopy.loading.label}
      >
        <Loader color="indigo" aria-hidden="true" />
        <Text fw={600}>{phaseCopy.loading.status}</Text>
        <Text c="var(--es-text-2)" size="sm" ta="center">
          {phaseCopy.loading.guidance}
        </Text>
      </Stack>
    ) : (
      <Stack align="stretch" gap="lg" aria-live="polite">
        <Stack align="center" gap="sm">
          <Title ref={headingRef} order={1} size="h2" ta="center" tabIndex={-1}>
            {phaseCopy.error.heading}
          </Title>
          <Text c="var(--es-text-2)" ta="center">
            {phaseCopy.error.guidance}
          </Text>
        </Stack>
        <Stack gap="xs">
          <Button
            fullWidth
            color="indigo"
            loading={retryBusy}
            disabled={retryBusy}
            aria-busy={retryBusy || undefined}
            style={{ minHeight: 44 }}
            onClick={() => void handleRetry()}
          >
            Try again
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
