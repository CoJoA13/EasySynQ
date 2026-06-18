import { Anchor, Group, Loader, SegmentedControl, Stack, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { ErrorState, LoadingState } from "../../lib/states";
import { RedlineViewer } from "../document/RedlineViewer";
import { VisualDiffViewer } from "../document/VisualDiffViewer";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { DcrStateBadge } from "./DcrStateBadge";
import { CHANGE_TYPE_LABEL } from "./labels";
import { useDcr } from "./hooks";
import { resolvePredecessor } from "./resolvePredecessor";

// S-dcr-ui-3: the page-image visual diff + text/metadata redline of a REVISE DCR's resulting version
// against the version it supersedes. Reuses the S-web-4b document diff components verbatim, pinned to
// the (predecessor → resulting) pair. The diff content is gated document.read_draft on the TARGET
// document (a separate key from changeRequest.read) — a reviewer without it sees a calm "no access".
export function DcrDiffPage() {
  const { id } = useParams();
  const dcrId = id ?? null;
  const { data: dcr, isLoading, isError, refetch } = useDcr(dcrId);
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") === "visual" ? "visual" : "text";

  const eligible =
    !!dcr &&
    dcr.change_type === "REVISE" &&
    dcr.resulting_version_id !== null &&
    dcr.target_document_id !== null;

  const versionsQ = useDocumentVersions(dcr?.target_document_id ?? null, eligible, {
    retry: false,
  });
  const pair = useMemo(
    () =>
      eligible && versionsQ.data
        ? resolvePredecessor(versionsQ.data, dcr.resulting_version_id as string)
        : null,
    [eligible, versionsQ.data, dcr],
  );
  const versionsForbidden = versionsQ.error instanceof ApiError && versionsQ.error.status === 403;

  function setMode(value: string) {
    setParams((p) => {
      p.set("mode", value);
      return p;
    });
  }

  const back = dcrId ? `/dcrs?dcr=${dcrId}` : "/dcrs";

  if (isLoading) return <LoadingState label="Loading change request" />;
  if (isError || !dcr) {
    return (
      <ErrorState
        title="Couldn't load this change request"
        message={
          <>
            It may have been removed, or you may not have access.{" "}
            <Anchor component={Link} to="/dcrs">
              Back to change requests
            </Anchor>
          </>
        }
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <Stack gap="lg">
      <div>
        <Anchor component={Link} to={back} size="sm">
          <span aria-hidden="true">← </span>Back to change request
        </Anchor>
      </div>
      <Group gap="sm" align="center">
        <Title order={2}>{dcr.identifier}</Title>
        <Text c="dimmed">{CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}</Text>
        <DcrStateBadge state={dcr.state} />
      </Group>

      {!eligible ? (
        <Text c="dimmed">
          No visual diff for this change request. A visual diff is available only for a Revise
          change once it has been implemented.
        </Text>
      ) : versionsQ.isLoading ? (
        <Loader size="sm" />
      ) : versionsForbidden ? (
        <Text c="dimmed">You don&apos;t have access to this document&apos;s versions.</Text>
      ) : versionsQ.isError ? (
        <Text c="red">Couldn&apos;t load the document&apos;s versions.</Text>
      ) : !pair ? (
        <Text c="dimmed">No prior version to compare against.</Text>
      ) : (
        <Stack gap="sm">
          <SegmentedControl
            size="xs"
            aria-label="Diff mode"
            value={mode}
            onChange={setMode}
            data={[
              { value: "text", label: "Text" },
              { value: "visual", label: "Visual" },
            ]}
            w="fit-content"
          />
          {mode === "visual" ? (
            <VisualDiffViewer
              documentId={dcr.target_document_id as string}
              fromVid={pair.from}
              toVid={pair.to}
            />
          ) : (
            <RedlineViewer
              documentId={dcr.target_document_id as string}
              fromVid={pair.from}
              toVid={pair.to}
            />
          )}
        </Stack>
      )}
    </Stack>
  );
}
