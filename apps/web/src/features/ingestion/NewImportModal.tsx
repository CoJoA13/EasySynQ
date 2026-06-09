import { Alert, Button, Group, Modal, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ImportRunCreate } from "../../lib/types";
import { useCreateImportRun } from "./hooks";

// The New-Import form (D-1): a typed source_root within the configured import mount (no directory
// picker — §10), an OCR toggle, and an optional profile. On 202 we hand the new run id up to the
// page controller (it then routes to /ingestion/:runId and polls ScanProgress). A 409 (a scan is
// already active) or a 422 (bad/escaping source root) is a calm inline message read from
// ApiError.message (the RFC 9457 detail/title) — never a red toast or a thrown stack (DP-6).
export function NewImportModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const [sourceRoot, setSourceRoot] = useState("");
  const [ocr, setOcr] = useState(false);
  const [profile, setProfile] = useState("");
  const create = useCreateImportRun();

  function reset() {
    setSourceRoot("");
    setOcr(false);
    setProfile("");
    create.reset();
  }
  function close() {
    reset();
    onClose();
  }
  function submit() {
    const root = sourceRoot.trim();
    if (root.length === 0) return;
    const body: ImportRunCreate = { source_root: root, ocr_enabled: ocr };
    const p = profile.trim();
    if (p.length > 0) body.profile = p;
    create.mutate(body, {
      onSuccess: (run) => {
        const id = run.id;
        reset();
        onClose();
        onCreated(id);
      },
    });
  }

  const errorMessage =
    create.error instanceof ApiError
      ? create.error.message
      : create.isError
        ? "Couldn't start the import. Please try again."
        : null;

  return (
    <Modal opened={opened} onClose={close} title="New import" size="lg" closeButtonProps={{ "aria-label": "Close new import dialog" }}>
      <Stack gap="md">
        <TextInput
          data-autofocus
          label="Source folder path"
          aria-label="Source folder path"
          placeholder="/srv/import/legacy-qms-share"
          description="A path within the configured import mount. The engine scans it read-only — nothing is controlled until you commit."
          value={sourceRoot}
          onChange={(e) => setSourceRoot(e.currentTarget.value)}
          required
        />
        <Switch
          label="Run OCR on scanned files"
          aria-label="Run OCR on scanned files"
          checked={ocr}
          onChange={(e) => setOcr(e.currentTarget.checked)}
        />
        <TextInput
          label="Profile (optional)"
          aria-label="Import profile"
          placeholder="default"
          value={profile}
          onChange={(e) => setProfile(e.currentTarget.value)}
        />
        {errorMessage && (
          <Alert color="gray" title="Couldn't start the import">
            {errorMessage}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={sourceRoot.trim().length === 0}
            loading={create.isPending}
          >
            Start import
          </Button>
        </Group>
        <Text size="xs" c="dimmed">
          The tool organizes; you decide. Review every item before committing to the vault.
        </Text>
      </Stack>
    </Modal>
  );
}
