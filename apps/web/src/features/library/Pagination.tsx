import { Button, Group, Select, Text } from "@mantine/core";
import { PAGE_SIZES } from "./filters";

// Page-size selector + prev/next pager driven by offset + page.has_more. No exact total (honest —
// the backend derives has_more without a COUNT). Prev/Next disable at the ends (state, not a
// permission gate). Page number is informational.
export function Pagination({
  offset,
  size,
  hasMore,
  onOffset,
  onSize,
}: {
  offset: number;
  size: number;
  hasMore: boolean;
  onOffset: (offset: number) => void;
  onSize: (size: number) => void;
}) {
  const page = Math.floor(offset / size) + 1;
  return (
    <Group justify="space-between">
      <Select
        aria-label="Page size"
        data={PAGE_SIZES.map((n) => ({ value: String(n), label: `${n} / page` }))}
        value={String(size)}
        onChange={(v) => onSize(Number(v ?? String(size)))}
        w={120}
        size="sm"
        allowDeselect={false}
      />
      <Group gap="xs">
        <Button
          variant="default"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - size))}
        >
          ‹ Prev
        </Button>
        <Text size="sm" c="dimmed">
          Page {page}
        </Text>
        <Button
          variant="default"
          size="sm"
          disabled={!hasMore}
          onClick={() => onOffset(offset + size)}
        >
          Next ›
        </Button>
      </Group>
    </Group>
  );
}
