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
