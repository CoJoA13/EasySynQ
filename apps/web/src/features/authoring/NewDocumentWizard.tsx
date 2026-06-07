import {
  Alert,
  Button,
  Container,
  Group,
  Select,
  Stack,
  Stepper,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { ApiError } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";
import { CheckInPanel } from "./CheckInPanel";
import { ClauseMapper } from "./ClauseMapper";
import { useClauseMappings, useCreateDocument, useSubmitReview } from "./hooks";

const CLASSIFICATIONS = ["Public", "Internal", "Confidential", "Restricted"];

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

// The guided New-Document journey (doc-11 §4.4 wizard): metadata → upload first version → map
// clauses → submit for review. The document is created at step 1 (so its id can drive the upload /
// clause / submit calls); abandoning afterward leaves an empty Draft (there is no discard in v1).
// Stops at submit-review — the reviewer inbox / approve / release are a different user's journey
// (S-web-5), since SoD-1 forbids the author from approving their own version.
export function NewDocumentWizard() {
  const navigate = useNavigate();
  const { data: types } = useDocumentTypes();
  const createDoc = useCreateDocument();
  const submitReview = useSubmitReview();

  const [active, setActive] = useState(0);
  const [doc, setDoc] = useState<DocumentSummary | null>(null);
  const [uploaded, setUploaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [typeId, setTypeId] = useState<string | null>(null);
  const [classification, setClassification] = useState<string>("Internal");
  const [areaCode, setAreaCode] = useState("");
  const [folderPath, setFolderPath] = useState("");

  const { data: mappings } = useClauseMappings(doc?.id ?? null, active >= 2 && doc !== null);
  const clauseCount = mappings?.length ?? 0;
  const typeName = doc ? types?.find((t) => t.id === doc.document_type_id)?.name : undefined;

  async function create() {
    if (!title.trim() || !typeId) return;
    setError(null);
    try {
      const created = await createDoc.mutateAsync({
        title: title.trim(),
        document_type_id: typeId,
        classification,
        area_code: areaCode.trim() || undefined,
        folder_path: folderPath.trim() || undefined,
      });
      setDoc(created);
      setActive(1);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function submit() {
    if (!doc) return;
    setError(null);
    try {
      await submitReview.mutateAsync(doc.id);
      navigate(`/library?detail=${doc.id}`);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <Container size="md" py="md">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-end">
          <Title order={1}>New document</Title>
          <Button variant="subtle" onClick={() => navigate("/library")}>
            Cancel
          </Button>
        </Group>

        {error && (
          <Alert color="red" title="Could not continue" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Stepper active={active} onStepClick={undefined}>
          <Stepper.Step label="Metadata" description="Identify the document">
            <Stack gap="md" mt="md">
              <Text size="sm" c="dimmed">
                The vault allocates the identifier ({"{TYPE}-{AREA}-{SEQ}"}); the document is created
                as a Draft. You become its owner.
              </Text>
              <TextInput
                label="Title"
                withAsterisk
                value={title}
                onChange={(e) => setTitle(e.currentTarget.value)}
              />
              <Select
                label="Type"
                withAsterisk
                placeholder="Pick a document type"
                data={(types ?? []).map((t) => ({ value: t.id, label: `${t.code} — ${t.name}` }))}
                value={typeId}
                onChange={setTypeId}
              />
              <Select
                label="Classification"
                data={CLASSIFICATIONS}
                value={classification}
                onChange={(v) => setClassification(v ?? "Internal")}
              />
              <TextInput
                label="Area code"
                description="Optional short token for the identifier (e.g. PUR). Defaults to GEN."
                value={areaCode}
                onChange={(e) =>
                  setAreaCode(e.currentTarget.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))
                }
                maxLength={8}
              />
              <TextInput
                label="Folder"
                description="Optional dotted scope path (e.g. SOPs.Purchasing)."
                value={folderPath}
                onChange={(e) => setFolderPath(e.currentTarget.value)}
              />
              <Group>
                <Button
                  loading={createDoc.isPending}
                  disabled={!title.trim() || !typeId}
                  onClick={() => void create()}
                >
                  Create &amp; continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Upload" description="First version">
            <Stack gap="md" mt="md">
              <Text size="sm" c="dimmed">
                Upload the document file as the first version (Rev A). Check it out, attach the file,
                and check it in.
              </Text>
              {doc && (
                <CheckInPanel
                  documentId={doc.id}
                  defaultSignificance="MAJOR"
                  onCheckedIn={() => setUploaded(true)}
                />
              )}
              <Group>
                <Button
                  variant="default"
                  disabled={!uploaded}
                  onClick={() => setActive(2)}
                >
                  Continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Clauses" description="Map ≥1 clause">
            <Stack gap="md" mt="md">
              {doc && <ClauseMapper documentId={doc.id} />}
              <Group>
                <Button variant="subtle" onClick={() => setActive(1)}>
                  Back
                </Button>
                <Button variant="default" disabled={clauseCount < 1} onClick={() => setActive(3)}>
                  Continue
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Submit" description="For review">
            <Stack gap="md" mt="md">
              <Text size="sm" c="dimmed">
                Review and submit for approval. After submission the document is{" "}
                <strong>In review</strong> and an approver decides — you cannot approve your own
                version (separation of duties).
              </Text>
              {doc && (
                <Stack gap={4}>
                  <Text>
                    <Text span ff="monospace" fw={600}>
                      {doc.identifier}
                    </Text>{" "}
                    — {doc.title}
                  </Text>
                  <Text size="sm" c="dimmed">
                    Type: {typeName ?? "—"} · {clauseCount} clause{clauseCount === 1 ? "" : "s"} mapped
                  </Text>
                </Stack>
              )}
              <Group>
                <Button variant="subtle" onClick={() => setActive(2)}>
                  Back
                </Button>
                <Button
                  color="teal"
                  loading={submitReview.isPending}
                  disabled={clauseCount < 1}
                  onClick={() => void submit()}
                >
                  Submit for review
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Stack>
    </Container>
  );
}
