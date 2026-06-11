import { Alert, Anchor, Card, SimpleGrid, Skeleton, Stack, Tabs, Text } from "@mantine/core";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { AuthorActions } from "../authoring/AuthorActions";
import { AcknowledgementsTab } from "./AcknowledgementsTab";
import { ApprovalsTab } from "./ApprovalsTab";
import { ArtifactHeader } from "./ArtifactHeader";
import { ControlMetadata } from "./ControlMetadata";
import { HistoryTab } from "./HistoryTab";
import { RenditionCard } from "./RenditionCard";
import { ReviewPeriodModal } from "./ReviewPeriodModal";
import { ReviewStateBadge } from "./ReviewStateBadge";
import { VersionCompare } from "./VersionCompare";
import { WhereUsedTab } from "./WhereUsedTab";
import { useDistribution } from "./ackHooks";
import { daysUntil } from "./reviewDates";
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
        <Text size="xs" c="dimmed" component="div">
          {sub}
        </Text>
      )}
    </Card>
  );
}

// S-web-4: the standalone read-only Document detail page (/documents/:id), promoting the S-web-2/3
// drawer to a full page (doc 11 §5.3 / §4.6 / §4.7). Reuses ArtifactHeader / AuthorActions (gated,
// D-A) / HistoryTab / WhereUsedTab / ControlMetadata, and adds the rendition card + the redline.
// S-ack-2: refactored to a real Mantine Tabs (Overview · History · Approvals · Where-used ·
// Acknowledgements) with the header + author actions + tiles persistent above; adds the Acknowledged
// coverage tile + the Acks tab (?tab=acks deep-linkable). The distribution GET fires on page load to
// drive the persistent tile (document.read, cheap), regardless of the active tab.
export function DocumentDetailPage() {
  const { id = null } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "overview";
  const setTab = (v: string | null) =>
    setSp(
      (prev) => {
        prev.set("tab", v ?? "overview");
        return prev;
      },
      { replace: true },
    );

  const { data: doc, isLoading, isError, error } = useDocument(id, { enabled: id !== null });
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const { data: versions } = useDocumentVersions(id, id !== null);
  const dist = useDistribution(id ?? "");
  const [reviewEditOpen, setReviewEditOpen] = useState(false);

  if (isLoading && !doc) {
    return (
      <Stack gap="md" aria-label="Loading document">
        <Skeleton height={40} width="60%" />
        <Skeleton height={20} width="40%" />
        <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
          {Array.from({ length: 5 }).map((_, i) => (
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
  const reviewDays = doc.next_review_due ? daysUntil(doc.next_review_due) : null;
  const cov = dist.data?.coverage ?? null;

  return (
    <Stack gap="lg">
      <ArtifactHeader doc={doc} typeName={typeName} ownerName={ownerName} />

      {/* Author actions (D-A): capability + state + lock gated; quiet-absent for readers (DP-6). */}
      <AuthorActions doc={doc} />

      <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
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
        <Tile
          label="Next review"
          value={
            reviewDays === null
              ? "—"
              : reviewDays >= 0
                ? `${reviewDays} days`
                : `${-reviewDays} days overdue`
          }
          sub={
            doc.next_review_due ? (
              <>
                {doc.next_review_due} <ReviewStateBadge state={doc.review_state} />
              </>
            ) : (
              "No scheduled review"
            )
          }
        />
        <Tile
          label="Acknowledged"
          value={cov === null ? "—" : cov.required === 0 ? "—" : `${cov.acknowledged} / ${cov.required}`}
          sub={
            cov === null
              ? "Not yet effective"
              : cov.required === 0
                ? "Not distributed"
                : `${cov.pending} pending`
          }
        />
      </SimpleGrid>

      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="history">History</Tabs.Tab>
          <Tabs.Tab value="approvals">Approvals</Tabs.Tab>
          <Tabs.Tab value="where-used">Where-used</Tabs.Tab>
          <Tabs.Tab value="acks">Acknowledgements</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="overview" pt="md">
          <Stack gap="lg">
            <RenditionCard doc={doc} />
            <Card withBorder>
              <Stack gap="sm">
                <Text fw={600}>Control metadata</Text>
                <ControlMetadata
                  doc={doc}
                  typeName={typeName}
                  ownerName={ownerName}
                  onEditReviewPeriod={
                    doc.capabilities?.manage_metadata ? () => setReviewEditOpen(true) : undefined
                  }
                />
              </Stack>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="history" pt="md">
          <Card withBorder>
            <Stack gap="md">
              <Text fw={600}>Version history</Text>
              <HistoryTab documentId={id} active={tab === "history"} />
              <VersionCompare documentId={doc.id} versions={versionList} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="approvals" pt="md">
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Approvals</Text>
              <ApprovalsTab doc={doc} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="where-used" pt="md">
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Where-used</Text>
              <WhereUsedTab documentId={id} active={tab === "where-used"} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="acks" pt="md">
          <AcknowledgementsTab documentId={doc.id} active={tab === "acks"} />
        </Tabs.Panel>
      </Tabs>

      {reviewEditOpen && (
        <ReviewPeriodModal doc={doc} opened onClose={() => setReviewEditOpen(false)} />
      )}
    </Stack>
  );
}
