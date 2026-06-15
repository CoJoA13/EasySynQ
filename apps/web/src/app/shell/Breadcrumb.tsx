import { Anchor, Breadcrumbs, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import type { DocumentSummary } from "../../lib/types";

const LABELS: Record<string, string> = {
  "": "Home",
  library: "Library",
  new: "New document",
  documents: "Document",
  tasks: "Task",
  compliance: "Compliance",
  capa: "Nonconformity & CAPA",
  complaints: "Complaints",
  ncrs: "NCRs",
  audits: "Internal audit",
  programme: "Programme",
  dcrs: "Change requests",
  diff: "Visual diff",
  ingestion: "Import",
  drift: "Drift",
  "superseded-copies": "Superseded copies",
  objectives: "Objectives",
  "management-reviews": "Management reviews",
  search: "Search",
};

export function Breadcrumb() {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);

  // S-web-4: on a /documents/:id route, REACTIVELY read the document the page loads so the crumb
  // re-renders from "Document" to the real identifier once ['document', id] is populated. A bare
  // getQueryData() read does NOT subscribe to cache changes, so a cold/bookmarked visit would stick
  // on the fallback — a fetch-less useQuery observer (enabled:false) on the same key is notified when
  // the page's useDocument fills the cache.
  const docIdx = segments.indexOf("documents");
  const docId = docIdx >= 0 && docIdx + 1 < segments.length ? segments[docIdx + 1] : null;
  const { data: doc } = useQuery<DocumentSummary>({
    queryKey: ["document", docId],
    queryFn: () => Promise.reject(new Error("breadcrumb does not fetch")),
    enabled: false,
  });

  const crumbs = [{ to: "/", label: "Home" }].concat(
    segments.map((seg, i) => {
      let label = LABELS[seg] ?? seg;
      if (i > 0 && segments[i - 1] === "documents") {
        label = doc?.identifier ?? "Document";
      }
      return { to: "/" + segments.slice(0, i + 1).join("/"), label };
    }),
  );
  return (
    <Breadcrumbs aria-label="Breadcrumb">
      {crumbs.map((c, i) =>
        i === crumbs.length - 1 ? (
          <Text key={`${i}-${c.to}`} c="dimmed">
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
