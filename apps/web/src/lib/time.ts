// Relative + timezone-explicit absolute time formatting. The codebase had no shared date util (the
// inline `toISOString().slice(0,10)` idiom); this is introduced for the status-board "as of" clocks
// (critique #2b) so freshness is legible and the timezone is never ambiguous (the `iso.slice(0,16)`
// wall-clock leak the drift board shipped). Pure given an explicit `now` → unit-testable.

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function toMillis(input: string | number): number {
  return typeof input === "number" ? input : new Date(input).getTime();
}

// Format an instant as the calendar date observed in the canonical organization timezone. Wire
// timestamps are UTC instants; slicing their first 10 characters silently shows the wrong day for
// organizations east/west of UTC. formatToParts keeps the output a stable YYYY-MM-DD regardless of
// runtime locale. If the timezone has not loaded (or a malformed value somehow arrives), preserve
// the timestamp's explicit date rather than throwing.
export function formatDateInTimeZone(input: string, timeZone: string | null | undefined): string {
  const fallback = /^(\d{4}-\d{2}-\d{2})/.exec(input)?.[1] ?? input;
  const instant = new Date(input);
  if (!timeZone || Number.isNaN(instant.getTime())) return fallback;

  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(instant);
    const value = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((part) => part.type === type)?.value;
    const year = value("year");
    const month = value("month");
    const day = value("day");
    return year && month && day ? `${year}-${month}-${day}` : fallback;
  } catch {
    return fallback;
  }
}

// A localised, timezone-EXPLICIT absolute timestamp (the `iso.slice(0,16)` ambiguity fix). `timeZoneName:
// "short"` appends the zone (e.g. "UTC" / "GMT+1"), so an exported/screenshotted board can be dated.
export function formatTimestamp(input: string | number): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(toMillis(input)));
}

// A compact "x ago" relative label for a status-board freshness stamp. Coarse buckets (a board doesn't
// need second precision); a future/skewed stamp clamps to "just now" (never "in the future"); anything
// older than a week falls back to the explicit absolute date.
export function formatRelativeTime(input: string | number, now: number = Date.now()): string {
  const then = toMillis(input);
  if (Number.isNaN(then)) return "";
  const diff = now - then;
  if (diff < MIN) return "just now";
  if (diff < HOUR) {
    const m = Math.floor(diff / MIN);
    return `${m} min ago`;
  }
  if (diff < DAY) {
    const h = Math.floor(diff / HOUR);
    return `${h} h ago`;
  }
  if (diff < 2 * DAY) return "yesterday";
  if (diff < 7 * DAY) {
    const d = Math.floor(diff / DAY);
    return `${d} days ago`;
  }
  return formatTimestamp(then);
}

// The wall-clock date and time observed in the canonical ORGANIZATION timezone, as a stable
// MM/DD/YY plus HH:MM plus the zone's own short name (R69's rail-foot clock).
//
// ⚠ This is the format the whole product is moving TO, and it currently disagrees with the rest of
// the interface. `formatDateInTimeZone` above — every register row and timeline, through
// `useOrgDate` — still emits a locale-independent YYYY-MM-DD, so a reader sees `09/02/26` in the
// rail beside `2026-09-02` in a table. R70 settles it: the US month/day/year reading in six-digit
// form is the standard, and the OTHER surfaces change to match — tracked as
// RES-DATE-TIME-DISPLAY-CONVERGENCE. So do NOT "fix" this one back to YYYY-MM-DD to resolve the
// mismatch; R70 rule 4 exists because that is the natural but backwards reading. The 24-hour time
// beside it is R70 rule 2, which `hour12`/`hourCycle` below pin and a named test asserts as a
// requirement rather than as an incidental. Deliberately NOT browser-local: records and audit
// events are stamped in org time, and `useOrgDate` already renders every register date that way, so
// a browser-local clock in the same frame would disagree with the timeline directly above it.
//
// Pure given an explicit `now`, so every boundary — midnight, a half-hour offset, a DST transition,
// a missing or malformed zone — is unit-testable with no browser and no clock. `hour12: false`
// and `hourCycle: "h23"` together pin 24-hour output with midnight as "00:00" regardless of runtime
// locale. ⚠ They are REDUNDANT, and that is measured rather than assumed: removing either one alone
// leaves all 15 tests green, and removing BOTH reddens four. Neither is individually load-bearing,
// so do not describe one as the guard — an earlier comment here credited `hourCycle`, a correction
// then credited `hour12`, and both statements were wrong.
export function formatOrgClock(
  now: number | Date,
  timeZone: string | null | undefined,
): { date: string; time: string; zone: string } | null {
  const instant = now instanceof Date ? now : new Date(now);
  if (!timeZone || Number.isNaN(instant.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      // The date is resolved in the ORG zone for the same reason the time is, and the boundary is
      // sharper: at 23:00 in a UTC-5 zone the UTC date is already tomorrow, so a browser-local date
      // under an "Organization time" label would name the wrong DAY, not merely the wrong hour.
      year: "2-digit",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      hourCycle: "h23",
      timeZoneName: "short",
    }).formatToParts(instant);
    const value = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((part) => part.type === type)?.value;
    const hour = value("hour");
    const minute = value("minute");
    const zone = value("timeZoneName");
    const year = value("year");
    const month = value("month");
    const day = value("day");
    if (!hour || !minute || !zone || !year || !month || !day) return null;
    return { date: `${month}/${day}/${year}`, time: `${hour}:${minute}`, zone };
  } catch {
    // An invalid IANA name throws a RangeError. A clock that cannot be trusted must not be shown at
    // all — rendering the browser's time under an org-time label would be worse than rendering
    // nothing, because it looks authoritative and is wrong.
    return null;
  }
}
