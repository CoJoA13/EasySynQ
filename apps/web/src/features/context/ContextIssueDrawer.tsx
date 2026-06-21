import { Box, Button, Divider, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { StatusBadge } from "../../lib/StatusBadge";
import {
  CATEGORY_LABEL,
  CATEGORY_TONE,
  CLASSIFICATION_GLYPH,
  CLASSIFICATION_LABEL,
  CLASSIFICATION_TONE,
  STATUS_LABEL,
  STATUS_TONE,
} from "./labels";
import { useContextIssue } from "./hooks";
import { EditIssueModal } from "./EditIssueModal";

// The context-issue detail drawer (the RiskDetailDrawer idiom, minus the CAPA-spawn seam — clause 4.1
// has no treatment axis). Prop-driven issueId, opened on issueId !== null; the page owns the ?issue=
// URL wiring. Context is ORG-LEVEL — no process probe; Edit gates on the page's server-computed
// can_manage (register.manage @ SYSTEM) AND the head being editable (no dead buttons).
export function ContextIssueDrawer({
  issueId,
  onClose,
  headEditable,
  canManage,
}: {
  issueId: string | null;
  onClose: () => void;
  headEditable: boolean;
  canManage: boolean;
}) {
  const { data: issue, isLoading, isError, forbidden, refetch } = useContextIssue(issueId);
  const [editOpen, setEditOpen] = useState(false);

  return (
    <DetailDrawer opened={issueId !== null} onClose={onClose} title="Context issue">
      {isLoading ? (
        <LoadingState label="Loading issue" />
      ) : isError || !issue ? (
        forbidden ? (
          <NoAccessState message="You don't have access to this context issue." />
        ) : (
          <ErrorState
            title="Couldn't load this issue"
            message="Something went wrong. Please try again."
            onRetry={() => refetch()}
          />
        )
      ) : (
        <Stack gap="md">
          <Group gap="xs" wrap="wrap">
            <StatusBadge
              tone={CLASSIFICATION_TONE[issue.classification]}
              glyph={CLASSIFICATION_GLYPH[issue.classification]}
              label={CLASSIFICATION_LABEL[issue.classification]}
              kind="Classification"
            />
            {issue.category && (
              <StatusBadge
                tone={CATEGORY_TONE[issue.category]}
                label={CATEGORY_LABEL[issue.category]}
                kind="SWOT"
              />
            )}
            <StatusBadge
              tone={STATUS_TONE[issue.status]}
              label={STATUS_LABEL[issue.status]}
              kind="Status"
            />
          </Group>
          <Text>{issue.description}</Text>

          <Divider />
          <Box>
            <Text size="sm" fw={600}>
              Last reviewed
            </Text>
            <Text size="sm" c={issue.last_reviewed_at ? undefined : "dimmed"}>
              {issue.last_reviewed_at ? issue.last_reviewed_at.slice(0, 10) : "Never reviewed."}
            </Text>
          </Box>

          {canManage && headEditable && (
            <>
              <Divider />
              <Group justify="flex-end">
                <Button variant="default" onClick={() => setEditOpen(true)}>
                  Edit issue
                </Button>
              </Group>
            </>
          )}
          {canManage && !headEditable && (
            <Text size="xs" c="dimmed">
              The register isn&rsquo;t in an editable state — issues are read-only. See the register
              banner for the current step.
            </Text>
          )}

          {editOpen && <EditIssueModal opened onClose={() => setEditOpen(false)} issue={issue} />}
        </Stack>
      )}
    </DetailDrawer>
  );
}
