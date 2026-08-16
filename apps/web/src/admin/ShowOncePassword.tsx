import { Alert, Button, Code, Group, Stack, Text, Title } from "@mantine/core";
import { useEffect, useRef } from "react";

export interface ShowOncePasswordProps {
  password: string;
  onDone: () => void;
  doneLabel?: string;
  description?: string;
  busy?: boolean;
}

const DEFAULT_DESCRIPTION =
  "Hand this to them directly. They must choose their own password at first login. This value is not stored and cannot be shown again — reissuing means resetting it.";

// S-user-create Task 7: the show-once temporary-password panel. The value lives in props ONLY —
// never persisted (no localStorage/sessionStorage, never a URL, never logged). The parent clears it
// from state on `onDone` so a reopened CreateUserModal never shows a stale credential.
export function ShowOncePassword({
  password,
  onDone,
  doneLabel = "Done",
  description = DEFAULT_DESCRIPTION,
  busy = false,
}: ShowOncePasswordProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [password]);

  return (
    <Stack gap="sm" miw={0} w="100%">
      <Alert color="yellow" aria-busy={busy || undefined}>
        <Stack gap="xs">
          <Title ref={headingRef} order={2} size="h3" tabIndex={-1}>
            Temporary password — shown once
          </Title>
          <Code
            style={{
              display: "block",
              fontSize: "1.1rem",
              letterSpacing: "0.05em",
              maxWidth: "100%",
              overflowWrap: "anywhere",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {password}
          </Code>
          <Text size="sm">{description}</Text>
          <Group wrap="wrap">
            <Button
              variant="light"
              aria-label="Copy temporary password"
              disabled={busy}
              style={{ minHeight: 44 }}
              onClick={() => void navigator.clipboard?.writeText(password)}
            >
              Copy
            </Button>
            <Button
              onClick={onDone}
              loading={busy}
              disabled={busy}
              aria-busy={busy || undefined}
              style={{ minHeight: 44 }}
            >
              {doneLabel}
            </Button>
          </Group>
        </Stack>
      </Alert>
    </Stack>
  );
}
