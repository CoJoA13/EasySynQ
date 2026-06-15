import { Alert, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError } from "./api";

// Critique #3 (harden) — the ONE shared confirm-on-irreversible primitive. Irreversible / WORM
// compliance acts (Release, MR/audit/CAPA Close, NCR disposition, Remove-output, bulk-Exclude) must
// restate their consequence and take a deliberate second click before mutating an audit-grade record.
//
// It owns its own `busy` + `error` state around an async `onConfirm`: the caller writes `onConfirm` to
// run the mutation AND close on success (`await m.mutateAsync(...); onCancel()`); on a thrown error the
// modal surfaces it and STAYS OPEN (the server's 409 — release_blocked / audit_close_blocked /
// ncr_already_dispositioned — lands here, calm and in-context). `mapError` lets a caller translate a
// known code to friendlier copy; the default uses the ApiError message.
export function ConfirmDestructive({
  opened,
  onCancel,
  onConfirm,
  title,
  consequence,
  confirmLabel,
  confirmColor = "red",
  irreversible = true,
  mapError,
}: {
  opened: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
  title: string;
  consequence: ReactNode;
  confirmLabel: string;
  confirmColor?: string;
  irreversible?: boolean;
  mapError?: (e: unknown) => string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Clear any prior error each time the dialog (re)opens.
  useEffect(() => {
    if (opened) setError(null);
  }, [opened]);

  async function run() {
    setError(null);
    setBusy(true);
    try {
      await onConfirm();
    } catch (e) {
      setError(
        mapError?.(e) ??
          (e instanceof ApiError ? e.message : "Something went wrong. Please retry."),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      opened={opened}
      // Never let an outside click / Escape dismiss mid-mutation.
      onClose={busy ? () => {} : onCancel}
      title={title}
      centered
    >
      <Stack gap="md">
        <Text size="sm">{consequence}</Text>
        {irreversible && (
          <Text size="sm" c="dimmed">
            This can&rsquo;t be undone.
          </Text>
        )}
        {error && <Alert color="red">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button color={confirmColor} onClick={() => void run()} loading={busy}>
            {confirmLabel}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
