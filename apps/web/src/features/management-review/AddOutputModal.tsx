import {
  Alert, Button, Group, Input, Modal, SegmentedControl, Select, Stack, Textarea, TextInput,
} from "@mantine/core";
import { useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import type { ReviewOutputCreateBody, ReviewOutputType } from "../../lib/types";
import { OUTPUT_LABEL } from "./labels";
import { useAddOutput } from "./mutations";

interface Props {
  opened: boolean;
  reviewId: string;
  onClose: () => void;
}

// The parent conditionally renders this ({ open && <AddOutputModal opened … /> }), so close
// unmounts it and the field state resets (the S-web-7d persistent-modal lesson). An ACTION requires
// an owner before save is enabled (the spawn target) — owner/due fields show only for an ACTION.
export function AddOutputModal({ opened, reviewId, onClose }: Props) {
  const add = useAddOutput();
  const { data: directory } = useUserDirectory();
  const [outputType, setOutputType] = useState<ReviewOutputType>("DECISION");
  const [description, setDescription] = useState("");
  const [ownerUserId, setOwnerUserId] = useState<string | null>(null);
  const [dueDate, setDueDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const isAction = outputType === "ACTION";
  const canSave = description.trim() !== "" && (!isAction || ownerUserId !== null);

  async function save() {
    setError(null);
    const body: ReviewOutputCreateBody = {
      output_type: outputType,
      description: description.trim(),
      owner_user_id: isAction ? ownerUserId : null,
      due_date: isAction && dueDate !== "" ? dueDate : null,
    };
    try {
      await add.mutateAsync({ id: reviewId, body });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong adding the output.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Add review output">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Input.Wrapper label="Type">
          <SegmentedControl
            fullWidth
            value={outputType}
            onChange={(v) => setOutputType(v as ReviewOutputType)}
            data={(["DECISION", "ACTION", "IMPROVEMENT"] as const).map((t) => ({
              value: t,
              label: OUTPUT_LABEL[t],
            }))}
          />
        </Input.Wrapper>
        <Textarea
          label="Description"
          required
          autosize
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        {isAction && (
          <>
            <Select
              label="Owner"
              required
              placeholder="Choose an owner"
              value={ownerUserId}
              onChange={setOwnerUserId}
              data={(directory ?? []).map((u) => ({
                value: u.id,
                label: u.display_name ?? u.id,
              }))}
              comboboxProps={{ keepMounted: false }}
            />
            <TextInput
              type="date"
              label="Due date"
              value={dueDate}
              onChange={(e) => setDueDate(e.currentTarget.value)}
            />
          </>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={add.isPending} disabled={!canSave}>
            Add output
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
