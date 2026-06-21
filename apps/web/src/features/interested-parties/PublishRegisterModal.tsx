import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { usePublishInterestedPartyRegister } from "./mutations";

interface Props {
  opened: boolean;
  onClose: () => void;
}

// Publish an interested-parties-register revision — freeze the working parties into a new version and
// submit it for approval. The change reason is OPTIONAL (the server defaults a system reason when
// omitted); it's recorded on the frozen version + shown to the approver.
//
// Parent renders {open && <PublishRegisterModal/>} so close unmounts + resets the draft (the
// persistently-mounted-modal trap). On a thrown ApiError (e.g. a 409 "no parties to publish" / "not
// editable") the Alert surfaces it and the modal STAYS OPEN.
export function PublishRegisterModal({ opened, onClose }: Props) {
  const publish = usePublishInterestedPartyRegister();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    try {
      await publish.mutateAsync({ change_reason: reason.trim() === "" ? null : reason.trim() });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong publishing the register.");
    }
  }

  return (
    <Modal
      opened={opened}
      // Never let Escape / a backdrop click dismiss mid-publish (the request continues; an eventual
      // error would be lost with the unmounted state) — the ConfirmDestructive posture (Codex P2).
      onClose={publish.isPending ? () => {} : onClose}
      title="Publish register revision"
      centered
    >
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">
          Freezes all interested parties into a new version, then submits it for approval.
        </Text>
        <Textarea
          label="Change reason"
          description="Optional — recorded on the version and shown to the approver."
          placeholder="e.g. Annual review of interested parties and their needs"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          autosize
          minRows={2}
          maxRows={5}
          maxLength={2000}
        />
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onClose} disabled={publish.isPending}>
            Cancel
          </Button>
          <Button onClick={() => void save()} loading={publish.isPending}>
            Publish
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
