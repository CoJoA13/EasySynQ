import { Alert, Button, Code, Group, Stack, Text } from "@mantine/core";

// S-user-create Task 7: the show-once temporary-password panel. The value lives in props ONLY —
// never persisted (no localStorage/sessionStorage, never a URL, never logged). The parent clears it
// from state on `onDone` so a reopened CreateUserModal never shows a stale credential.
export function ShowOncePassword({ password, onDone }: { password: string; onDone: () => void }) {
  return (
    <Stack gap="sm">
      <Alert color="yellow" title="Temporary password — shown once">
        <Stack gap="xs">
          <Code style={{ fontSize: "1.1rem", letterSpacing: "0.05em" }}>{password}</Code>
          <Text size="sm">
            Hand this to them directly. They must choose their own password at first login. This
            value is not stored and cannot be shown again — reissuing means resetting it.
          </Text>
          <Group>
            <Button
              variant="light"
              aria-label="Copy temporary password"
              onClick={() => void navigator.clipboard?.writeText(password)}
            >
              Copy
            </Button>
            <Button onClick={onDone}>Done</Button>
          </Group>
        </Stack>
      </Alert>
    </Stack>
  );
}
