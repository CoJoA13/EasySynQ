import { Alert, Button, Group, Loader, Modal, Select, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { useDocuments } from "../library/useDocuments";
import { useImplementDcr } from "./mutations";

// CREATE-implement (ui-4): a CREATE DCR is the change-control record for a NEW controlled document
// authored out-of-band in the document workspace (Draft→Approved). Implement RELEASES that Approved
// version, so the user picks the approved document this DCR creates; we resolve its Approved version
// client-side and POST resulting_version_id. The capability (changeRequest.implement) can't know the
// picked doc's document.release/SoD-2 scope, so any 403/409 is surfaced calmly (submit-and-show).
export function ImplementCreateDcrModal({
  dcrId,
  onClose,
}: {
  dcrId: string;
  onClose: () => void;
}) {
  const m = useImplementDcr(dcrId);
  const [docId, setDocId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // S-doc-filters: the server narrows the candidate set — only never-released (no effective version),
  // non-managed-subtype (not OBJ/MR) Approved documents come back, so every returned row is a valid
  // CREATE-implement target and the page never wastes slots on invalid rows. The
  // _resolve_implement_version guard stays the submit-time backstop.
  const { data: docsPage, isError: docsError } = useDocuments(
    { current_state: "Approved", has_effective_version: false, managed_subtype: false },
    { limit: 100, offset: 0 },
  );
  const options = useMemo(
    () =>
      (docsPage?.data ?? []).map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` })),
    [docsPage],
  );

  const versions = useDocumentVersions(docId, docId !== null, { retry: false });
  const approvedVersion = (versions.data ?? []).find((v) => v.version_state === "Approved");
  const noApproved =
    docId !== null && !versions.isLoading && !versions.isError && approvedVersion === undefined;
  // A release-capable user who lacks document.read_draft gets a 403 resolving the version; surface it
  // (don't leave a silently-disabled button — Codex).
  const versionsError = docId !== null && versions.isError;

  async function submit() {
    if (approvedVersion === undefined) return;
    setError(null);
    try {
      await m.mutateAsync({ resulting_version_id: approvedVersion.id });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not implement the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Implement new-document change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">
          Pick the approved document authored to fulfil this change request. Implementing releases
          its approved version and links it here.
        </Text>
        {docsError ? (
          <Alert color="red">Couldn&apos;t load documents — you may not have access.</Alert>
        ) : options.length === 0 ? (
          <Text size="sm" c="dimmed">
            No approved documents to link. Author the new document in the workspace first, then
            return here to implement.
          </Text>
        ) : (
          <Select
            label="New document"
            required
            searchable
            placeholder="Pick the approved document this change request creates"
            value={docId}
            onChange={setDocId}
            data={options}
            nothingFoundMessage="No matching documents"
            comboboxProps={{ keepMounted: false }}
          />
        )}
        {docId !== null && versions.isLoading && <Loader size="sm" />}
        {noApproved && (
          <Text size="sm" c="red">
            That document has no approved version to release. Approve it first.
          </Text>
        )}
        {versionsError && (
          <Text size="sm" c="red">
            Couldn&apos;t load this document&apos;s versions — this step needs draft-read access to
            resolve the version to release. Ask someone with document access to implement.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Not yet
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={approvedVersion === undefined || m.isPending}
          >
            Implement
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
