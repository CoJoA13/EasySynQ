import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type {
  InterestedPartyCreateBody,
  InterestedPartyInfluence,
  InterestedPartyType,
} from "../../lib/types";
import { INFLUENCE_LABEL, PARTY_TYPE_SINGULAR } from "./labels";
import { PARTY_TYPE_ORDER } from "./board";
import { useCreateParty } from "./mutations";

const PARTY_TYPE_OPTIONS = PARTY_TYPE_ORDER.map((t) => ({
  value: t,
  label: PARTY_TYPE_SINGULAR[t],
}));
const INFLUENCE_OPTIONS = (Object.keys(INFLUENCE_LABEL) as InterestedPartyInfluence[]).map((i) => ({
  value: i,
  label: INFLUENCE_LABEL[i],
}));

// Create an interested party. Clause 4.2 is ORG-LEVEL — no process picker (unlike risk). party_type is
// the required ISO spine (a Select, default customer); influence is optional/clearable (the nullable
// ordered axis); party_name + needs_expectations are required; last reviewed is an optional date. A new
// party is always "active" (no status field on create). Conditionally mounted by the page so close
// discards the draft.
export function NewPartyModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useCreateParty();
  const [partyType, setPartyType] = useState<InterestedPartyType>("customer");
  const [partyName, setPartyName] = useState("");
  const [needs, setNeeds] = useState("");
  const [influence, setInfluence] = useState<InterestedPartyInfluence | null>(null);
  const [lastReviewed, setLastReviewed] = useState("");
  const [error, setError] = useState<string | null>(null);

  const canSubmit = partyName.trim() !== "" && needs.trim() !== "";

  async function submit() {
    setError(null);
    if (!canSubmit) return;
    try {
      const created = await m.mutateAsync({
        party_type: partyType,
        party_name: partyName.trim(),
        needs_expectations: needs.trim(),
        influence,
        last_reviewed_at: lastReviewed || null,
      } satisfies InterestedPartyCreateBody);
      onCreated(created.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the interested party.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New interested party">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Select
          label="Party type"
          aria-label="Party type"
          value={partyType}
          onChange={(v) => v && setPartyType(v as InterestedPartyType)}
          data={PARTY_TYPE_OPTIONS}
          allowDeselect={false}
          comboboxProps={{ keepMounted: false }}
        />
        <TextInput
          label="Party name"
          required
          value={partyName}
          onChange={(e) => setPartyName(e.currentTarget.value)}
        />
        <Textarea
          label="Needs & expectations"
          required
          value={needs}
          onChange={(e) => setNeeds(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Select
          label="Influence (optional)"
          placeholder="Unspecified"
          clearable
          value={influence}
          onChange={(v) => setInfluence(v as InterestedPartyInfluence | null)}
          data={INFLUENCE_OPTIONS}
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
            Create party
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
