import { Alert } from "@mantine/core";

/**
 * U14: the register listings bound their pre-authorization scan window, so a very large register
 * may omit its oldest rows. The API returns `truncated` when that window filled; an audit-facing
 * register must say so rather than present a partial list as if it were complete.
 */
export function TruncationNotice({ truncated, noun }: { truncated: boolean; noun: string }) {
  if (!truncated) return null;
  return (
    <Alert color="yellow" variant="light" mt="xs" role="status">
      Showing the most recent {noun}. Older entries may exist that were not loaded.
    </Alert>
  );
}
