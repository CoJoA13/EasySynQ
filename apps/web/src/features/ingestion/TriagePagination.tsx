import { Button, Group, Text } from "@mantine/core";
import { FILES_PAGE_SIZE } from "./filters";

// Offset pager at the FIXED FILES_PAGE_SIZE (mirrors the Library Pagination shape, but the /files
// contract returns no total/has_more — the caller derives `hasMore = files.length === FILES_PAGE_SIZE`
// and passes the page-row count + the bucket total from run.counts). "Showing X–Y of N" only when
// `total` is provided; otherwise the honest prev/next-only pager. Prev/Next disable at the ends
// (queue state, not a permission gate).
export function TriagePagination({
  offset,
  hasMore,
  onOffset,
  pageCount,
  total,
}: {
  offset: number;
  hasMore: boolean;
  onOffset: (offset: number) => void;
  pageCount?: number;
  total?: number;
}) {
  const onPage = pageCount ?? 0;
  const from = onPage > 0 ? offset + 1 : 0;
  const to = offset + onPage;
  return (
    <Group justify="space-between">
      {total !== undefined && onPage > 0 ? (
        <Text size="sm" c="dimmed">
          Showing {from}–{to} of {total} in this queue
        </Text>
      ) : (
        <span />
      )}
      <Group gap="xs">
        <Button
          variant="default"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - FILES_PAGE_SIZE))}
        >
          ‹ Prev
        </Button>
        <Button
          variant="default"
          size="sm"
          disabled={!hasMore}
          onClick={() => onOffset(offset + FILES_PAGE_SIZE)}
        >
          Next ›
        </Button>
      </Group>
    </Group>
  );
}
