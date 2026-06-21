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
  InterestedParty,
  InterestedPartyInfluence,
  InterestedPartyStatus,
  InterestedPartyType,
  InterestedPartyUpdateBody,
} from "../../lib/types";
import { INFLUENCE_LABEL, PARTY_TYPE_SINGULAR } from "./labels";
import { PARTY_TYPE_ORDER } from "./board";
import { useUpdateParty } from "./mutations";

const PARTY_TYPE_OPTIONS = PARTY_TYPE_ORDER.map((t) => ({
  value: t,
  label: PARTY_TYPE_SINGULAR[t],
}));
const INFLUENCE_OPTIONS = (Object.keys(INFLUENCE_LABEL) as InterestedPartyInfluence[]).map((i) => ({
  value: i,
  label: INFLUENCE_LABEL[i],
}));

// Edit an interested party — a partial PATCH sending ONLY changed fields (the backend's exclude_unset;
// omitted ≠ null). An explicit null clears influence / last_reviewed_at. Edit adds the status control
// (active/closed — retire by closing, never delete). The drawer gates mounting on can_manage @ SYSTEM
// AND the head being editable; conditionally mounted so close discards the draft.
export function EditPartyModal({
  opened,
  onClose,
  party,
}: {
  opened: boolean;
  onClose: () => void;
  party: InterestedParty;
}) {
  const m = useUpdateParty(party.id);
  const [partyType, setPartyType] = useState<InterestedPartyType>(party.party_type);
  const [partyName, setPartyName] = useState(party.party_name);
  const [needs, setNeeds] = useState(party.needs_expectations);
  const [influence, setInfluence] = useState<InterestedPartyInfluence | null>(party.influence);
  const [status, setStatus] = useState<InterestedPartyStatus>(party.status);
  const [lastReviewed, setLastReviewed] = useState(party.last_reviewed_at?.slice(0, 10) ?? "");
  const [error, setError] = useState<string | null>(null);

  function buildPatch(): InterestedPartyUpdateBody {
    const patch: InterestedPartyUpdateBody = {};
    if (partyType !== party.party_type) patch.party_type = partyType;
    if (partyName.trim() && partyName.trim() !== party.party_name)
      patch.party_name = partyName.trim();
    if (needs.trim() && needs.trim() !== party.needs_expectations)
      patch.needs_expectations = needs.trim();
    const inf = influence ?? null;
    if (inf !== (party.influence ?? null)) patch.influence = inf;
    if (status !== party.status) patch.status = status;
    const lr = lastReviewed || null;
    const rowLr = party.last_reviewed_at ? party.last_reviewed_at.slice(0, 10) : null;
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
      setError(e instanceof ApiError ? e.message : "Could not save the interested party.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Edit interested party">
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
        <SegmentedControl
          aria-label="Status"
          value={status}
          onChange={(v) => setStatus(v as InterestedPartyStatus)}
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
            disabled={!dirty || partyName.trim() === "" || needs.trim() === ""}
          >
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
