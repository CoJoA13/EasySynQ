import { Group, Select, Stack, Text } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import type { DocumentVersion } from "../../lib/types";
import { RedlineViewer } from "./RedlineViewer";

// S-web-4: the doc 11 §4.6 inline "Compare" picker — two version selects whose choice is the
// page's URL state (?from=&to=), so the redline is deep-linkable/shareable. The RedlineViewer
// renders below once a distinct pair is chosen. Hidden when there is nothing to compare (<2 versions).
export function VersionCompare({
  documentId,
  versions,
}: {
  documentId: string;
  versions: DocumentVersion[];
}) {
  const [params, setParams] = useSearchParams();
  const from = params.get("from");
  const to = params.get("to");

  if (versions.length < 2) return null;

  const options = versions.map((v) => ({
    value: v.id,
    label: `${v.revision_label} · ${v.version_state}`,
  }));

  function set(key: "from" | "to", value: string | null) {
    setParams((p) => {
      if (value) p.set(key, value);
      else p.delete(key);
      return p;
    });
  }

  const showViewer = !!from && !!to && from !== to;

  return (
    <Stack gap="sm">
      <Group gap="sm" align="flex-end">
        <Select
          label="Compare from"
          placeholder="Select version"
          data={options}
          value={from}
          onChange={(v) => set("from", v)}
          maw={240}
        />
        <Text c="dimmed" mb={6}>
          →
        </Text>
        <Select
          label="to"
          placeholder="Select version"
          data={options}
          value={to}
          onChange={(v) => set("to", v)}
          maw={240}
        />
      </Group>
      {!!from && from === to && (
        <Text size="xs" c="dimmed">
          Pick two different versions to compare.
        </Text>
      )}
      {showViewer && <RedlineViewer documentId={documentId} fromVid={from} toVid={to} />}
    </Stack>
  );
}
