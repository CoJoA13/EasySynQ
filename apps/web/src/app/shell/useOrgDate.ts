import { useCallback } from "react";
import { formatDateInTimeZone } from "../../lib/time";
import { useMe } from "./useMe";

/**
 * U20 (the C11 class): render a wire instant as the calendar date observed in the canonical
 * ORG timezone. The `new Date(iso).toISOString().slice(0, 10)` idiom this replaces shows the
 * wrong day for every organization east or west of UTC — an evening event in Berlin, or a
 * morning one in Denver, is filed under the neighbouring date on timelines and registers.
 */
export function useOrgDate(): (iso: string) => string {
  const { data: me } = useMe();
  const tz = me?.org_timezone;
  return useCallback((iso: string) => formatDateInTimeZone(iso, tz), [tz]);
}
