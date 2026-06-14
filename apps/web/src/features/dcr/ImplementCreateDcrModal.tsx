import { Alert, Button, Group, Loader, Modal, Select, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
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

  const { data: docsPage, isError: docsError } = useDocuments(
    { current_state: "Approved" },
    { limit: 200, offset: 0 },
  );
  // Managed subtypes (Quality Objectives, Management Reviews) have their own create/release
  // workspaces and are rejected by the CREATE-implement guard server-side — keep them out of the
  // picker so an invalid candidate never appears (Codex). Form templates etc. stay valid.
  const { data: docTypes } = useDocumentTypes();
  const managedTypeIds = useMemo(
    () =>
      new Set((docTypes ?? []).filter((t) => t.code === "OBJ" || t.code === "MR").map((t) => t.id)),
    [docTypes],
  );
  const options = useMemo(
    () =>
      (docsPage?.data ?? [])
        // A CREATE DCR releases the INITIAL version of a NEW document: exclude approved REVISIONS of
        // existing docs (current_effective_version_id set) and managed subtypes (server-guarded too).
        .filter(
          (d) =>
            d.kind === "DOCUMENT" &&
            d.current_effective_version_id === null &&
            !(d.document_type_id !== null && managedTypeIds.has(d.document_type_id)),
        )
        .map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` })),
    [docsPage, managedTypeIds],
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
