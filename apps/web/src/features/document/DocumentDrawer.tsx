import { Anchor, Tabs } from "@mantine/core";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { LoadingState } from "../../lib/states";
import type { DocumentSummary } from "../../lib/types";
import { AuthorActions } from "../authoring/AuthorActions";
import { ArtifactHeader } from "./ArtifactHeader";
import { HistoryTab } from "./HistoryTab";
import { OverviewTab } from "./OverviewTab";
import { WhereUsedTab } from "./WhereUsedTab";
import { useDocument } from "./useDocument";

// The deep-linkable (?detail=<id>) document detail drawer (DP-3). The artifact header (DP-5) renders
// ABOVE the tabs and always shows — even if a tab errors. Tabs lazy-load (keepMounted=false + the
// per-tab `active` enable gate): opening the drawer fetches only the document, not every tab.
export function DocumentDrawer({
  documentId,
  seed,
  onClose,
}: {
  documentId: string | null;
  seed?: DocumentSummary;
  onClose: () => void;
}) {
  const opened = documentId !== null;
  const { data: doc, isLoading } = useDocument(documentId, { enabled: opened, seed });
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const [tab, setTab] = useState<string>("overview");

  // Reset to Overview whenever the drawer switches to a different document.
  useEffect(() => {
    setTab("overview");
  }, [documentId]);

  const typeName = doc ? types?.find((t) => t.id === doc.document_type_id)?.name : undefined;
  const ownerName = doc
    ? (directory?.find((u) => u.id === doc.owner_user_id)?.display_name ?? undefined)
    : undefined;

  return (
    <DetailDrawer opened={opened} onClose={onClose} title={doc?.identifier ?? "Document"}>
      {isLoading && !doc && <LoadingState label="Loading document" />}
      {doc && (
        <>
          <ArtifactHeader doc={doc} typeName={typeName} ownerName={ownerName} />
          {documentId && (
            <Anchor component={Link} to={`/documents/${documentId}`} size="sm">
              ⤢ Open full page
            </Anchor>
          )}
          <AuthorActions doc={doc} />
          <Tabs value={tab} onChange={(v) => setTab(v ?? "overview")} keepMounted={false} mt="md">
            <Tabs.List>
              <Tabs.Tab value="overview">Overview</Tabs.Tab>
              <Tabs.Tab value="history">History</Tabs.Tab>
              <Tabs.Tab value="whereused">Where-used</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="overview" pt="md">
              <OverviewTab doc={doc} typeName={typeName} ownerName={ownerName} />
            </Tabs.Panel>
            <Tabs.Panel value="history" pt="md">
              <HistoryTab documentId={documentId} active={tab === "history"} />
            </Tabs.Panel>
            <Tabs.Panel value="whereused" pt="md">
              <WhereUsedTab documentId={documentId} active={tab === "whereused"} />
            </Tabs.Panel>
          </Tabs>
        </>
      )}
    </DetailDrawer>
  );
}
