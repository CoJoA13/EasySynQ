import { Box, Button, Divider, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { StatusBadge } from "../../lib/StatusBadge";
import {
  INFLUENCE_GLYPH,
  INFLUENCE_LABEL,
  INFLUENCE_TONE,
  PARTY_TYPE_SINGULAR,
  PARTY_TYPE_TONE,
  STATUS_LABEL,
  STATUS_TONE,
} from "./labels";
import { useInterestedParty } from "./hooks";
import { EditPartyModal } from "./EditPartyModal";

// The interested-party detail drawer (the context-issue drawer idiom, minus the CAPA-spawn seam —
// clause 4.2 has no treatment axis). Prop-driven partyId, opened on partyId !== null; the page owns the
// ?party= URL wiring. Clause 4.2 is ORG-LEVEL — no process probe; Edit gates on the page's
// server-computed can_manage (register.manage @ SYSTEM) AND the head being editable (no dead buttons).
export function InterestedPartyDrawer({
  partyId,
  onClose,
  headEditable,
  canManage,
}: {
  partyId: string | null;
  onClose: () => void;
  headEditable: boolean;
  canManage: boolean;
}) {
  const { data: party, isLoading, isError, forbidden, refetch } = useInterestedParty(partyId);
  const [editOpen, setEditOpen] = useState(false);

  return (
    <DetailDrawer opened={partyId !== null} onClose={onClose} title="Interested party">
      {isLoading ? (
        <LoadingState label="Loading interested party" />
      ) : isError || !party ? (
        forbidden ? (
          <NoAccessState message="You don't have access to this interested party." />
        ) : (
          <ErrorState
            title="Couldn't load this interested party"
            message="Something went wrong. Please try again."
            onRetry={() => refetch()}
          />
        )
      ) : (
        <Stack gap="md">
          <Group gap="xs" wrap="wrap">
            <StatusBadge
              tone={PARTY_TYPE_TONE[party.party_type]}
              label={PARTY_TYPE_SINGULAR[party.party_type]}
              kind="Party type"
            />
            {party.influence ? (
              <StatusBadge
                tone={INFLUENCE_TONE[party.influence]}
                glyph={INFLUENCE_GLYPH[party.influence]}
                label={INFLUENCE_LABEL[party.influence]}
                kind="Influence"
              />
            ) : (
              <StatusBadge tone="neutral" label="Influence unspecified" kind="Influence" />
            )}
            <StatusBadge
              tone={STATUS_TONE[party.status]}
              label={STATUS_LABEL[party.status]}
              kind="Status"
            />
          </Group>

          <Title order={4}>{party.party_name}</Title>

          <Box>
            <Text size="sm" fw={600}>
              Needs &amp; expectations
            </Text>
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {party.needs_expectations}
            </Text>
          </Box>

          <Divider />
          <Box>
            <Text size="sm" fw={600}>
              Last reviewed
            </Text>
            <Text size="sm" c={party.last_reviewed_at ? undefined : "dimmed"}>
              {party.last_reviewed_at ? party.last_reviewed_at.slice(0, 10) : "Never reviewed."}
            </Text>
          </Box>

          {canManage && headEditable && (
            <>
              <Divider />
              <Group justify="flex-end">
                <Button variant="default" onClick={() => setEditOpen(true)}>
                  Edit party
                </Button>
              </Group>
            </>
          )}
          {canManage && !headEditable && (
            <Text size="xs" c="dimmed">
              The register isn&rsquo;t in an editable state — interested parties are read-only. See
              the register banner for the current step.
            </Text>
          )}

          {editOpen && <EditPartyModal opened onClose={() => setEditOpen(false)} party={party} />}
        </Stack>
      )}
    </DetailDrawer>
  );
}
