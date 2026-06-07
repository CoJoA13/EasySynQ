import { Anchor, Breadcrumbs, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";

const LABELS: Record<string, string> = {
  "": "Home",
  library: "Library",
  new: "New document",
  documents: "Document",
  tasks: "Task",
};

export function Breadcrumb() {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);
  const crumbs = [{ to: "/", label: "Home" }].concat(
    segments.map((seg, i) => ({
      to: "/" + segments.slice(0, i + 1).join("/"),
      label: LABELS[seg] ?? seg,
    })),
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
