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

  const { data: docsPage, isError: docsError } = useDocuments(
    { current_state: "Approved" },
    { limit: 200, offset: 0 },
  );
  const options = useMemo(
    () =>
      (docsPage?.data ?? [])
        .filter((d) => d.kind === "DOCUMENT")
        .map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` })),
    [docsPage],
  );

  const versions = useDocumentVersions(docId, docId !== null, { retry: false });
  const approvedVersion = (versions.data ?? []).find((v) => v.version_state === "Approved");
  const noApproved =
    docId !== null && !versions.isLoading && !versions.isError && approvedVersion === undefined;

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
