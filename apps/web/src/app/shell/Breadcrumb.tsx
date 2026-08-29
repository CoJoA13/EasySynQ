import { Anchor, Breadcrumbs, Text } from "@mantine/core";
import { IconChevronRight } from "../../lib/icons";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import type { DocumentSummary, RecordDetail } from "../../lib/types";
import { useFreshRecordData } from "../../features/records/hooks";

const LABELS: Record<string, string> = {
  "": "Home",
  library: "Library",
  new: "New document",
  documents: "Document",
  tasks: "Task",
  notifications: "Notifications",
  settings: "Settings",
  compliance: "Compliance",
  reports: "Reports",
  "document-control": "Controlled document register",
  capa: "Nonconformity and CAPA",
  complaints: "Complaints",
  ncrs: "NCRs",
  audits: "Internal audit",
  program: "Program",
  dcrs: "Change requests",
  diff: "Visual diff",
  imports: "Import",
  ingestion: "Import",
  drift: "Drift",
  "superseded-copies": "Superseded copies",
  objectives: "Objectives",
  "management-reviews": "Management reviews",
  improvement: "Improvement",
  risks: "Risk & opportunity register",
  context: "Context of the organization",
  "interested-parties": "Interested parties",
  records: "Records",
  search: "Search",
};

const DETAIL_LABELS: Record<string, string> = {
  documents: "Document",
  tasks: "Task",
  audits: "Audit",
  imports: "Import run",
  ingestion: "Import run",
  objectives: "Objective",
  "management-reviews": "Management review",
  records: "Record",
  dcrs: "Change request",
};

function humanizeSegment(segment: string): string {
  const words = segment.replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Some route trees have a presentational parent segment but no page at that cumulative path. Keep
// those crumbs as orientation text instead of sending users through the catch-all redirect. The DCR
// diff is the one dynamic case: /dcrs exists and /dcrs/:id/diff exists, but /dcrs/:id does not.
const NON_ROUTE_CRUMBS = new Set(["/documents", "/reports", "/settings"]);

function isRoutableCrumb(path: string): boolean {
  return !NON_ROUTE_CRUMBS.has(path) && !/^\/dcrs\/[^/]+$/.test(path);
}

function RecordBreadcrumbLabel({ recordId }: { recordId: string }) {
  const recordQuery = useQuery<RecordDetail>({
    queryKey: ["record", recordId],
    queryFn: () => Promise.reject(new Error("breadcrumb does not fetch")),
    enabled: false,
  });
  const record = useFreshRecordData(recordId, recordQuery);
  return <>{record?.identifier ?? DETAIL_LABELS.records}</>;
}

// The separator is decorative: the list already conveys the trail, so exposing a chevron per gap
// would just add noise to every screen-reader pass over the breadcrumb.
function BreadcrumbSeparator() {
  return <IconChevronRight size={14} style={{ display: "block", color: "var(--es-text-muted)" }} />;
}

export interface BreadcrumbProps {
  notFound?: boolean;
}

export function Breadcrumb({ notFound = false }: BreadcrumbProps) {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);

  // S-web-4: on a /documents/:id route, REACTIVELY read the document the page loads so the crumb
  // re-renders from "Document" to the real identifier once ['document', id] is populated. A bare
  // getQueryData() read does NOT subscribe to cache changes, so a cold/bookmarked visit would stick
  // on the fallback — a fetch-less useQuery observer (enabled:false) on the same key is notified when
  // the page's useDocument fills the cache.
  const docIdx = segments.indexOf("documents");
  const docId =
    !notFound && docIdx >= 0 && docIdx + 1 < segments.length ? segments[docIdx + 1] : null;
  const { data: doc } = useQuery<DocumentSummary>({
    queryKey: ["document", docId],
    queryFn: () => Promise.reject(new Error("breadcrumb does not fetch")),
    enabled: false,
  });
  if (notFound) {
    return (
      <Breadcrumbs aria-label="Breadcrumb" separator={<BreadcrumbSeparator />}>
        <Anchor component={Link} to="/">
          Home
        </Anchor>
        <Text c="dimmed">Page not found</Text>
      </Breadcrumbs>
    );
  }

  const crumbs: { to: string; label: ReactNode; linkable: boolean }[] = [
    { to: "/", label: "Home", linkable: true },
    ...segments.map((seg, i) => {
      const parent = i > 0 ? segments[i - 1] : null;
      let label: ReactNode = LABELS[seg];
      if (!label && parent === "documents") {
        label = doc?.identifier ?? DETAIL_LABELS.documents;
      } else if (!label && parent === "records") {
        label = <RecordBreadcrumbLabel key={seg} recordId={seg} />;
      } else if (!label && parent) {
        label = DETAIL_LABELS[parent];
      }
      label ??= humanizeSegment(seg);
      const to = "/" + segments.slice(0, i + 1).join("/");
      return { to, label, linkable: isRoutableCrumb(to) };
    }),
  ];
  return (
    <Breadcrumbs aria-label="Breadcrumb" separator={<BreadcrumbSeparator />}>
      {crumbs.map((c, i) =>
        i === crumbs.length - 1 || !c.linkable ? (
          <Text key={`${i}-${c.to}`} c={i === crumbs.length - 1 ? "dimmed" : undefined}>
            {c.label}
          </Text>
        ) : (
          <Anchor key={`${i}-${c.to}`} component={Link} to={c.to}>
            {c.label}
          </Anchor>
        ),
      )}
    </Breadcrumbs>
  );
}
