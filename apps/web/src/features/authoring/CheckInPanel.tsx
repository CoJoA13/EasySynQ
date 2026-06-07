import {
  Alert,
  Anchor,
  Button,
  FileInput,
  Group,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import type { ChangeSignificance, CheckinResult } from "../../lib/types";
import { useBreakLock, useCheckout, useUploadAndCheckin } from "./hooks";

// The doc-11 §5.4 governed check-out → edit → check-in card (reused by the New-Document wizard's
// upload step AND the drawer's Author actions). EasySynQ is not an in-app editor (N4): you check out
// (acquire the lock), download the working file, edit it externally, then upload + check in a new
// immutable Draft version. The change reason + MAJOR/MINOR significance are mandatory (INV-3).
function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

export function CheckInPanel({
  documentId,
  sourceVersionId,
  defaultSignificance = "MINOR",
  onCheckedIn,
}: {
  documentId: string;
  sourceVersionId?: string | null;
  defaultSignificance?: ChangeSignificance;
  onCheckedIn?: (version: CheckinResult) => void;
}) {
  const api = useApi();
  const checkout = useCheckout();
  const breakLock = useBreakLock();
  const checkin = useUploadAndCheckin();

  const [checkedOut, setCheckedOut] = useState(false);
  const [lockHolder, setLockHolder] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [significance, setSignificance] = useState<ChangeSignificance>(defaultSignificance);

  async function doCheckout() {
    setError(null);
    setLockHolder(null);
    try {
      await checkout.mutateAsync(documentId);
      setCheckedOut(true);
    } catch (e) {
      if (e instanceof ApiError && e.code === "lock_conflict") setLockHolder(e.message);
      else setError(errMsg(e));
    }
  }

  async function doBreakLock() {
    setError(null);
    try {
      await breakLock.mutateAsync(documentId);
      setLockHolder(null);
      await doCheckout(); // retry now that the lock is cleared (the working copy is preserved, R9)
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function downloadWorkingCopy() {
    if (!sourceVersionId) return;
    try {
      const { download_url } = await api.get<{ download_url: string }>(
        `/api/v1/documents/${documentId}/versions/${sourceVersionId}/download`,
      );
      window.open(download_url, "_blank", "noopener,noreferrer");
    } catch {
      /* quiet — a transient presign failure is non-fatal */
    }
  }

  async function doCheckin() {
    if (!file || !reason.trim()) return;
    setError(null);
    try {
      const version = await checkin.mutateAsync({
        documentId,
        file,
        changeReason: reason.trim(),
        changeSignificance: significance,
      });
      setFile(null);
      setReason("");
      onCheckedIn?.(version);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <Stack gap="sm">
      {error && (
        <Alert color="red" title="Check-in problem" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {!checkedOut ? (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Editing is a governed check-out: you lock the document, edit the file externally, then
            check it back in as a new Draft revision. Others keep seeing the current effective copy.
          </Text>
          {lockHolder ? (
            <Alert color="yellow" title="Document is checked out">
              <Stack gap="xs">
                <Text size="sm">{lockHolder}</Text>
                <Group>
                  <Button
                    size="sm"
                    variant="default"
                    loading={breakLock.isPending || checkout.isPending}
                    onClick={() => void doBreakLock()}
                  >
                    Force unlock &amp; check out
                  </Button>
                </Group>
              </Stack>
            </Alert>
          ) : (
            <Group>
              <Button loading={checkout.isPending} onClick={() => void doCheckout()}>
                ⎘ Check out to edit
              </Button>
            </Group>
          )}
        </Stack>
      ) : (
        <Stack gap="sm">
          <Text size="sm" c="teal">
            ✓ Checked out by you — edit the file, then check it back in.
          </Text>
          {sourceVersionId && (
            <Anchor component="button" type="button" size="sm" onClick={() => void downloadWorkingCopy()}>
              ⤓ Download working copy
            </Anchor>
          )}
          <FileInput
            label="Upload revised file"
            placeholder="Choose a file"
            value={file}
            onChange={setFile}
            clearable
          />
          <Textarea
            label="Change reason / summary"
            description="Required — recorded on the version history (INV-3)."
            withAsterisk
            autosize
            minRows={2}
            value={reason}
            onChange={(e) => setReason(e.currentTarget.value)}
          />
          <div>
            <Text size="sm" fw={500} mb={4}>
              Change significance
            </Text>
            <SegmentedControl
              aria-label="Change significance"
              value={significance}
              onChange={(v) => setSignificance(v as ChangeSignificance)}
              data={[
                { label: "Major", value: "MAJOR" },
                { label: "Minor", value: "MINOR" },
              ]}
            />
          </div>
          <Group>
            <Button
              loading={checkin.isPending}
              disabled={!file || !reason.trim()}
              onClick={() => void doCheckin()}
            >
              Check in as Draft
            </Button>
          </Group>
        </Stack>
      )}
    </Stack>
  );
}
