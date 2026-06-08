import { Anchor, Breadcrumbs, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import type { DocumentSummary } from "../../lib/types";

const LABELS: Record<string, string> = {
  "": "Home",
  library: "Library",
  new: "New document",
  documents: "Document",
  tasks: "Task",
};

export function Breadcrumb() {
  const { pathname } = useLocation();
  const qc = useQueryClient();
  const segments = pathname.split("/").filter(Boolean);
  const crumbs = [{ to: "/", label: "Home" }].concat(
    segments.map((seg, i) => {
      // S-web-4: a /documents/:id leaf resolves to the document identifier (from the query cache),
      // not the raw UUID; degrades to the generic "Document" label when not yet cached.
      let label = LABELS[seg] ?? seg;
      if (i > 0 && segments[i - 1] === "documents") {
        const doc = qc.getQueryData<DocumentSummary>(["document", seg]);
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
