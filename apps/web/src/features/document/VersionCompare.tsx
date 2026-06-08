import { Group, Select, Stack, Text } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import type { DocumentVersion } from "../../lib/types";
import { RedlineViewer } from "./RedlineViewer";

// S-web-4: the doc 11 §4.6 inline "Compare" picker — two version selects whose choice is the page's
// URL state (?from=&to=), so the redline is deep-linkable/shareable. On a COLD visit (no URL pair) it
// DEFAULTS to the prior → newest revision pair (the primary "what changed in the latest rev" view);
// the RedlineViewer renders below for any distinct pair. Hidden when there's nothing to compare.
export function VersionCompare({
  documentId,
  versions,
}: {
  documentId: string;
  versions: DocumentVersion[];
}) {
  const [params, setParams] = useSearchParams();

  if (versions.length < 2) return null;

  // Newest first → [0] = newest (governing candidate), [1] = the prior revision (the default pair).
  const ordered = [...versions].sort((a, b) => b.version_seq - a.version_seq);
  const from = params.get("from") ?? ordered[1]?.id ?? null;
  const to = params.get("to") ?? ordered[0]?.id ?? null;

  const options = ordered.map((v) => ({
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
