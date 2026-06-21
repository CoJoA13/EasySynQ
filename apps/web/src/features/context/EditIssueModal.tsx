import {
  Alert,
  Button,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type {
  ContextCategory,
  ContextClassification,
  ContextIssue,
  ContextStatus,
  ContextUpdateBody,
} from "../../lib/types";
import { CATEGORY_LABEL } from "./labels";
import { useUpdateIssue } from "./mutations";

const CATEGORY_OPTIONS = (Object.keys(CATEGORY_LABEL) as ContextCategory[]).map((c) => ({
  value: c,
  label: CATEGORY_LABEL[c],
}));

// Edit a context issue — a partial PATCH sending ONLY changed fields (the backend's exclude_unset;
// omitted ≠ null). An explicit null clears category / last_reviewed_at. Edit adds the status control
// (active/closed — retire by closing, never delete). The drawer gates mounting on can_manage @ SYSTEM
// AND the head being editable; conditionally mounted so close discards the draft.
export function EditIssueModal({
  opened,
  onClose,
  issue,
}: {
  opened: boolean;
  onClose: () => void;
  issue: ContextIssue;
}) {
  const m = useUpdateIssue(issue.id);
  const [classification, setClassification] = useState<ContextClassification>(issue.classification);
  const [description, setDescription] = useState(issue.description);
  const [category, setCategory] = useState<ContextCategory | null>(issue.category);
  const [status, setStatus] = useState<ContextStatus>(issue.status);
  const [lastReviewed, setLastReviewed] = useState(issue.last_reviewed_at?.slice(0, 10) ?? "");
  const [error, setError] = useState<string | null>(null);

  function buildPatch(): ContextUpdateBody {
    const patch: ContextUpdateBody = {};
    if (classification !== issue.classification) patch.classification = classification;
    if (description.trim() && description.trim() !== issue.description)
      patch.description = description.trim();
    const cat = category ?? null;
    if (cat !== (issue.category ?? null)) patch.category = cat;
    if (status !== issue.status) patch.status = status;
    const lr = lastReviewed || null;
    const rowLr = issue.last_reviewed_at ? issue.last_reviewed_at.slice(0, 10) : null;
    if (lr !== rowLr) patch.last_reviewed_at = lr;
    return patch;
  }

  const patch = buildPatch();
  const dirty = Object.keys(patch).length > 0;

  async function submit() {
    setError(null);
    if (!dirty) {
      onClose();
      return;
    }
    try {
      await m.mutateAsync(patch);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the issue.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Edit context issue">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <SegmentedControl
          aria-label="Classification"
          value={classification}
          onChange={(v) => setClassification(v as ContextClassification)}
          data={[
            { value: "internal", label: "Internal" },
            { value: "external", label: "External" },
          ]}
        />
        <Textarea
          label="Description"
          required
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Select
          label="SWOT category (optional)"
          placeholder="No category"
          clearable
          value={category}
          onChange={(v) => setCategory(v as ContextCategory | null)}
          data={CATEGORY_OPTIONS}
          comboboxProps={{ keepMounted: false }}
        />
        <SegmentedControl
          aria-label="Status"
          value={status}
          onChange={(v) => setStatus(v as ContextStatus)}
          data={[
            { value: "active", label: "Active" },
            { value: "closed", label: "Closed" },
          ]}
        />
        <TextInput
          label="Last reviewed"
          type="date"
          value={lastReviewed}
          onChange={(e) => setLastReviewed(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!dirty || description.trim() === ""}
          >
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
