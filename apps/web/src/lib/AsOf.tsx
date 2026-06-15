import { Text } from "@mantine/core";
import { formatRelativeTime, formatTimestamp } from "./time";

// A calm freshness stamp for status boards (critique #2b / P2) — Olsen needs provable currency and
// Mara needs to trust the numbers before acting, in a product whose thesis is proving currency. `at`
// is a ms epoch (React-Query `dataUpdatedAt`) or a server scan time in ms; null/0 renders nothing
// (nothing has loaded yet). The relative label sits inline; the full timezone-explicit timestamp is in
// the `title` tooltip so a screenshot/export can still be dated.
export function AsOf({
  at,
  prefix = "Updated",
}: {
  at: number | null | undefined;
  prefix?: string;
}) {
  if (!at) return null;
  return (
    <Text size="xs" c="dimmed" title={formatTimestamp(at)}>
      {prefix} {formatRelativeTime(at)}
    </Text>
  );
}
