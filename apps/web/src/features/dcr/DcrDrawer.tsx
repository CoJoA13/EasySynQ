import { Alert, Anchor, Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { DirectoryUser } from "../../lib/types";
import { useDocument } from "../document/useDocument";
import { DcrImpactTable } from "./DcrImpactTable";
import { DcrStageTimeline } from "./DcrStageTimeline";
import { DcrStateBadge } from "./DcrStateBadge";
import { CHANGE_TYPE_LABEL, REASON_LABEL, SOURCE_LABEL } from "./labels";
import { useDcr, useDcrImpact } from "./hooks";

function nameOf(userId: string, directory: DirectoryUser[]): string {
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}
function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      {typeof children === "string" ? <Text size="sm">{children}</Text> : children}
    </div>
  );
}

export function DcrDrawer({ dcrId, onClose }: { dcrId: string | null; onClose: () => void }) {
  const { data: dcr, isLoading, isError } = useDcr(dcrId);
  const { data: impact } = useDcrImpact(dcrId);
  const { data: directoryData } = useUserDirectory();
  const directory = directoryData ?? [];
  const targetId = dcr?.target_document_id ?? null;
  // A 403/404 on the target is an EXPECTED degrade (the requester may lack read on the target doc) —
  // retry:false keeps the calm bare-id fallback without re-hammering a deterministic deny.
  const { data: targetDoc } = useDocument(targetId, { enabled: targetId !== null, retry: false });

  return (
    <DetailDrawer
      opened={dcrId !== null}
      onClose={onClose}
      title={
        // Gate the header on !isError too: a failed refetch can leave stale cached data, and we must
        // not show an out-of-date identifier above an error body (the CapaDrawer precedent).
        dcr && !isError ? (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              {dcr.identifier}
            </Text>
            <Title order={4}>{CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}</Title>
          </Stack>
        ) : (
          "Change request"
        )
      }
    >
      {isLoading ? (
        <Loader />
      ) : isError || !dcr ? (
        <Alert color="red" title="Couldn't load this change request">
          It may have been removed, or you may not have access. Close this panel and try again.
        </Alert>
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <DcrStateBadge state={dcr.state} />
            <Badge variant="light" color="gray">
              {CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}
            </Badge>
            <Badge variant="light" color="gray">
              {dcr.change_significance}
            </Badge>
            <Badge variant="light" color="gray">
              {REASON_LABEL[dcr.reason_class] ?? dcr.reason_class}
            </Badge>
          </Group>

          <Field label="Reason">{dcr.reason_text}</Field>

          <Field label="Target document">
            {dcr.target_document_id ? (
              <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                {targetDoc ? `${targetDoc.identifier} — ${targetDoc.title}` : dcr.target_document_id}
              </Anchor>
            ) : (
              <Text size="sm">New document (no target)</Text>
            )}
          </Field>

          {dcr.source_link_type ? (
            <Field label="Source">
              {dcr.source_link_type === "capa" && dcr.source_link_id ? (
                <Anchor component={Link} to={`/capa?capa=${dcr.source_link_id}`}>
                  {SOURCE_LABEL.capa}
                </Anchor>
              ) : (
                <Text size="sm">
                  {SOURCE_LABEL[dcr.source_link_type]}
                  {dcr.source_link_id ? ` · ${dcr.source_link_id.slice(0, 8)}…` : ""}
                </Text>
              )}
            </Field>
          ) : null}

          {dcr.resulting_version_id ? (
            <Field label="Resulting version">
              {/* Links to the document, not the version: there is no SPA version route and a bare
                  version_id can't be resolved to its document_id client-side (verified). For CREATE
                  (no target_document_id) the new doc's id isn't exposed by _dcr → show the id, no link. */}
              {dcr.target_document_id ? (
                <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                  View document
                </Anchor>
              ) : (
                <Text size="sm">{dcr.resulting_version_id.slice(0, 8)}… (new document)</Text>
              )}
            </Field>
          ) : null}

          {dcr.proposed_effective_from ? (
            <Field label="Proposed effective from">{formatDate(dcr.proposed_effective_from)}</Field>
          ) : null}

          {dcr.decision ? <Field label="Decision">{dcr.decision}</Field> : null}

          <Field label="Raised by">
            {`${nameOf(dcr.created_by, directory)} · ${formatDate(dcr.created_at)}`}
          </Field>

          <div>
            <Title order={5} mb="xs">
              Impact assessment
            </Title>
            <DcrImpactTable impact={impact ?? []} />
          </div>

          <div>
            <Title order={5} mb="xs">
              History
            </Title>
            <DcrStageTimeline events={dcr.stage_events} directory={directory} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
