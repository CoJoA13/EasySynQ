import { Alert, Anchor, Card, Grid, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { AuthorActions } from "../authoring/AuthorActions";
import { ApprovalsTab } from "./ApprovalsTab";
import { ArtifactHeader } from "./ArtifactHeader";
import { ControlMetadata } from "./ControlMetadata";
import { HistoryTab } from "./HistoryTab";
import { RenditionCard } from "./RenditionCard";
import { VersionCompare } from "./VersionCompare";
import { WhereUsedTab } from "./WhereUsedTab";
import { useDocument } from "./useDocument";
import { useDocumentVersions } from "./useDocumentVersions";

function Tile({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <Card withBorder padding="sm">
      <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
      {sub && (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      )}
    </Card>
  );
}

// S-web-4: the standalone read-only Document detail page (/documents/:id), promoting the S-web-2/3
// drawer to a full page (doc 11 §5.3 / §4.6 / §4.7). Reuses ArtifactHeader / AuthorActions (gated,
// D-A) / HistoryTab / WhereUsedTab / ControlMetadata, and adds the rendition card + the redline.
// Honest deferrals: Approvals/Acks/Audit tabs, the visual diff (S-web-4b), next-review (drift).
export function DocumentDetailPage() {
  const { id = null } = useParams();
  const { data: doc, isLoading, isError, error } = useDocument(id, { enabled: id !== null });
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const { data: versions } = useDocumentVersions(id, id !== null);

  if (isLoading && !doc) {
    return (
      <Stack gap="md" aria-label="Loading document">
        <Skeleton height={40} width="60%" />
        <Skeleton height={20} width="40%" />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={72} />
          ))}
        </SimpleGrid>
        <Skeleton height={240} />
      </Stack>
    );
  }

  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    const msg =
      status === 403
        ? "You don't have access to this document."
        : status === 404
          ? "This document does not exist."
          : "Could not load this document.";
    return (
      <Alert color={status === 403 ? "yellow" : "red"} title="Document unavailable">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">{msg}</Text>
          <Anchor component={Link} to="/library">
            ← Back to the Library
          </Anchor>
        </Stack>
      </Alert>
    );
  }

  const typeName = types?.find((t) => t.id === doc.document_type_id)?.name;
  const ownerName = directory?.find((u) => u.id === doc.owner_user_id)?.display_name ?? undefined;
  const versionList = versions ?? [];
  const governingRev = versionList.find(
    (v) => v.id === doc.current_effective_version_id,
  )?.revision_label;
  const effectiveDate = doc.effective_from ? doc.effective_from.slice(0, 10) : null;

  return (
    <Stack gap="lg">
      <ArtifactHeader doc={doc} typeName={typeName} ownerName={ownerName} />

      {/* Author actions (D-A): capability + state + lock gated; quiet-absent for readers (DP-6). */}
      <AuthorActions doc={doc} />

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Tile
          label="Governing revision"
          value={governingRev ?? (doc.current_effective_version_id ? "Effective" : "—")}
          sub={effectiveDate ? `Effective ${effectiveDate}` : "Not yet effective"}
        />
        <Tile
          label="Mapped clauses"
          value={(doc.clause_refs ?? []).join(", ") || "—"}
          sub="ISO 9001:2015"
        />
        <Tile
          label="Versions"
          value={versionList.length || "—"}
          sub={versionList.length ? "retained · newest first" : "history not in scope"}
        />
      </SimpleGrid>

      <Grid gutter="lg" align="flex-start">
        {/* Left: the controlled rendition + where-used. */}
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="lg">
            <RenditionCard doc={doc} />
            <Card withBorder>
              <Stack gap="sm">
                <Text fw={600}>Where-used</Text>
                <WhereUsedTab documentId={id} active={true} />
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>

        {/* Right: the version history + compare/redline, and the control metadata. */}
        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="lg">
            <Card withBorder>
              <Stack gap="sm">
                <Text fw={600}>Approvals</Text>
                <ApprovalsTab doc={doc} />
              </Stack>
            </Card>
            <Card withBorder>
              <Stack gap="md">
                <Text fw={600}>Version history</Text>
                <HistoryTab documentId={id} active={true} />
                <VersionCompare documentId={doc.id} versions={versionList} />
              </Stack>
            </Card>
            <Card withBorder>
              <Stack gap="sm">
                <Text fw={600}>Control metadata</Text>
                <ControlMetadata doc={doc} typeName={typeName} ownerName={ownerName} />
              </Stack>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
