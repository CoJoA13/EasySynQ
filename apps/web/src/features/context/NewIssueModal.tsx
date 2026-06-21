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
import type { ContextCategory, ContextClassification, ContextCreateBody } from "../../lib/types";
import { CATEGORY_LABEL } from "./labels";
import { useCreateIssue } from "./mutations";

const CATEGORY_OPTIONS = (Object.keys(CATEGORY_LABEL) as ContextCategory[]).map((c) => ({
  value: c,
  label: CATEGORY_LABEL[c],
}));

// Create a context issue. Clause 4.1 is ORG-LEVEL — no process picker (unlike risk). classification is
// the required ISO spine (a SegmentedControl, default internal); the SWOT category is optional/clearable
// (nullable); last reviewed is an optional date. A new issue is always "active" (no status field on
// create). Conditionally mounted by the page so close discards the draft.
export function NewIssueModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useCreateIssue();
  const [classification, setClassification] = useState<ContextClassification>("internal");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<ContextCategory | null>(null);
  const [lastReviewed, setLastReviewed] = useState("");
  const [error, setError] = useState<string | null>(null);

  const canSubmit = description.trim() !== "";

  async function submit() {
    setError(null);
    if (!canSubmit) return;
    try {
      const created = await m.mutateAsync({
        classification,
        description: description.trim(),
        category,
        last_reviewed_at: lastReviewed || null,
      } satisfies ContextCreateBody);
      onCreated(created.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the issue.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New context issue">
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
        <TextInput
          label="Last reviewed (optional)"
          type="date"
          value={lastReviewed}
          onChange={(e) => setLastReviewed(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!canSubmit}>
            Create issue
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
