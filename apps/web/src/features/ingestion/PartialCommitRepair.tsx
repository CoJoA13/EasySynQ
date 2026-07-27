import { Alert, Button, Card, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { useMemo, useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import type { ImportDecisionAfter } from "../../lib/types";
import { useFileDecision, useImportFile } from "./hooks";

interface CommitBlocker {
  type: string;
  file_id: string;
  owner?: string;
  identifier?: string;
}

interface RepairGroup {
  fileId: string;
  blockers: CommitBlocker[];
}

function repairGroups(error: unknown): RepairGroup[] {
  if (!(error instanceof ApiError) || error.code !== "commit_blocked") return [];
  const raw = error.problem?.["blocking"];
  if (!Array.isArray(raw)) return [];

  const byFile = new Map<string, CommitBlocker[]>();
  for (const entry of raw) {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) continue;
    const blocker = entry as Record<string, unknown>;
    const type = blocker["type"];
    const fileId = blocker["file_id"];
    if (typeof type !== "string" || typeof fileId !== "string") continue;
    const normalized: CommitBlocker = { type, file_id: fileId };
    if (typeof blocker["owner"] === "string") normalized.owner = blocker["owner"];
    if (typeof blocker["identifier"] === "string") {
      normalized.identifier = blocker["identifier"];
    }
    const existing = byFile.get(fileId) ?? [];
    existing.push(normalized);
    byFile.set(fileId, existing);
  }
  return [...byFile.entries()].map(([fileId, blockers]) => ({ fileId, blockers }));
}

function ownerLabel(user: { id: string; display_name: string | null }): string {
  const suffix = user.id.slice(-8);
  return user.display_name ? `${user.display_name} · ID …${suffix}` : `ID ${user.id}`;
}

function blockerLabel(type: string): string {
  if (type === "blank_identifier") return "The identifier is blank.";
  if (type === "owner_ambiguous") return "The owner reference matches more than one user.";
  if (type === "owner_not_found") return "The owner reference is no longer valid.";
  return type.replace(/_/g, " ").replace(/^\w/, (char) => char.toUpperCase());
}

function RepairItem({ runId, group }: { runId: string; group: RepairGroup }) {
  const { data: detail, isLoading: detailLoading } = useImportFile(runId, group.fileId);
  const { data: directoryUsers = [], isLoading: directoryLoading } = useUserDirectory();
  const decision = useFileDecision(runId);
  const [identifier, setIdentifier] = useState("");
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const needsIdentifier = group.blockers.some((blocker) => blocker.type === "blank_identifier");
  const needsOwner = group.blockers.some(
    (blocker) => blocker.type === "owner_not_found" || blocker.type === "owner_ambiguous",
  );
  const filename = detail?.filename ?? group.fileId;
  const committed = detail?.commit?.result === "success" || detail?.commit?.result === "noop";
  const repairable = needsIdentifier || needsOwner;
  const ownerOptions = useMemo(
    () => directoryUsers.map((user) => ({ value: user.id, label: ownerLabel(user) })),
    [directoryUsers],
  );
  const valid =
    (!needsIdentifier || identifier.trim().length > 0) && (!needsOwner || ownerId !== null);

  const save = () => {
    const after: ImportDecisionAfter = {};
    if (needsIdentifier) after.identifier = identifier.trim();
    if (needsOwner && ownerId !== null) after.owner = ownerId;
    setSaved(false);
    void decision
      .mutateAsync({
        fileId: group.fileId,
        body: { action: "correct", after },
        idempotencyKey: crypto.randomUUID(),
      })
      .then(() => setSaved(true))
      .catch(() => {
        // The mutation retains the ApiError for the inline alert below.
      });
  };

  return (
    <Card withBorder padding="md" component="section" aria-label={`Repair ${filename}`}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Text fw={600}>{detailLoading ? "Loading affected item…" : filename}</Text>
          <Text size="xs" c="dimmed" ff="monospace">
            {group.fileId}
          </Text>
        </Stack>

        {group.blockers.map((blocker, index) => (
          <Text key={`${blocker.type}-${index}`} size="sm">
            {blockerLabel(blocker.type)}
            {blocker.owner ? ` Current reference: ${blocker.owner}` : ""}
          </Text>
        ))}

        {committed ? (
          <Alert color="gray" title="Already committed">
            This item is immutable in the vault. Refresh the run before attempting another repair.
          </Alert>
        ) : !repairable ? (
          <Alert color="gray" title="No editor is available for this blocker">
            Refresh the run or contact an administrator before resuming. The item has not been
            changed.
          </Alert>
        ) : (
          <>
            {needsIdentifier && (
              <TextInput
                label={`New identifier for ${filename}`}
                value={identifier}
                onChange={(event) => {
                  setIdentifier(event.currentTarget.value);
                  setSaved(false);
                }}
                placeholder="Enter a non-blank identifier"
              />
            )}
            {needsOwner && (
              <Select
                label={`New owner for ${filename}`}
                data={ownerOptions}
                value={ownerId}
                onChange={(value) => {
                  setOwnerId(value);
                  setSaved(false);
                }}
                placeholder={directoryLoading ? "Loading users…" : "Choose an active user"}
                disabled={directoryLoading}
                searchable
              />
            )}

            {decision.error && (
              <Alert color="red" title="Correction failed">
                {decision.error instanceof ApiError
                  ? decision.error.message
                  : "Something went wrong. Please retry."}
              </Alert>
            )}
            {saved && (
              <Alert color="green" title="Correction saved">
                Resume the commit after all listed items have been corrected.
              </Alert>
            )}

            <Group>
              <Button
                onClick={save}
                disabled={!valid || decision.isPending}
                loading={decision.isPending}
              >
                Save correction for {filename}
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Card>
  );
}

export function PartialCommitRepair({ runId, error }: { runId: string; error: unknown }) {
  const groups = repairGroups(error);
  const message =
    error instanceof ApiError ? error.message : "The remaining items could not be committed.";

  return (
    <Stack gap="md" component="section" aria-label="Partial commit repairs">
      <Alert color="red" title={groups.length > 0 ? "Resume needs corrections" : "Resume failed"}>
        {message}
        {groups.length > 0 && (
          <Text size="sm" mt="xs">
            Correct the affected items below, then choose Resume commit again. Documents already in
            the vault are not changed.
          </Text>
        )}
      </Alert>
      {groups.map((group) => (
        <RepairItem key={group.fileId} runId={runId} group={group} />
      ))}
    </Stack>
  );
}
