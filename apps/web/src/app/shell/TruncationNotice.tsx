import { Alert } from "@mantine/core";

/**
 * U14: the register listings bound their pre-authorization scan window, so a very large register
 * may omit its oldest rows. The API returns `truncated` when that window filled; an audit-facing
 * register must say so rather than present a partial list as if it were complete.
 *
 * The message now names the remedy. Saying only "older entries may exist" was honest but left the
 * reader stuck; the registers accept server-side date filters that narrow BEFORE the window, so
 * those entries are reachable.
 */
export function TruncationNotice({ truncated, noun }: { truncated: boolean; noun: string }) {
  if (!truncated) return null;
  return (
    <Alert color="yellow" variant="light" mt="xs" role="status">
      Showing the most recent {noun}. Older entries exist beyond this window — narrow the date range
      above to reach them.
    </Alert>
  );
}
