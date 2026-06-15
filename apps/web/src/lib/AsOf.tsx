import { Text } from "@mantine/core";
import { useEffect, useState } from "react";
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
  // Re-render on an interval so the relative label ("5 min ago") doesn't FREEZE on a long-open
  // dashboard (Codex #144 P2) — without a tick it only re-computes on an unrelated re-render, so a
  // board left open keeps saying "just now" long after that stops being true. 30s granularity is
  // ample for the minute-resolution label; the tooltip always carries the exact timestamp.
  const [, tick] = useState(0);
  useEffect(() => {
    if (!at) return;
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [at]);
  if (!at) return null;
  return (
    <Text size="xs" c="dimmed" title={formatTimestamp(at)}>
      {prefix} {formatRelativeTime(at)}
    </Text>
  );
}
