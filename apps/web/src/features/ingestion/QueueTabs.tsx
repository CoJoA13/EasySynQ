import { Badge, Tabs } from "@mantine/core";
import { QUEUES, type IngestionQueue } from "./filters";

// The five confidence/decision queue tabs (Needs-decision / Medium / High / Quarantine / Already-in-
// vault), single-sourced from QUEUES (so order + countKey live in filters.ts). Presentational: the
// active queue comes in via `value`, a pick is reported via `onChange`; each tab badge reads its count
// from `counts[q.countKey]` with a `?? 0` fallback (noUncheckedIndexedAccess + a missing key). Real
// Mantine Tabs gives tablist/tab semantics for keyboard + screen-reader navigation.
export function QueueTabs({
  counts,
  value,
  onChange,
}: {
  counts: Record<string, number>;
  value: IngestionQueue;
  onChange: (q: IngestionQueue) => void;
}) {
  return (
    <Tabs
      value={value}
      onChange={(v) => {
        if (v) onChange(v as IngestionQueue);
      }}
      aria-label="Review queues"
    >
      <Tabs.List>
        {QUEUES.map((q) => (
          <Tabs.Tab
            key={q.value}
            value={q.value}
            rightSection={
              <Badge size="sm" variant="light" circle>
                {counts[q.countKey] ?? 0}
              </Badge>
            }
          >
            {q.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {/* Empty panels satisfy aria-controls validity (content rendered by ReviewCockpit). */}
      {QUEUES.map((q) => (
        <Tabs.Panel key={q.value} value={q.value} />
      ))}
    </Tabs>
  );
}
