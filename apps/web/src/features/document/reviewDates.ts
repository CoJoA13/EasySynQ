// Whole-day distance to a DATE-only next_review_due, in the BROWSER's timezone — display sugar for
// the "Days to review" tile. The server's review_state is the org-tz-authoritative signal; the two
// can differ by ±1 day across timezones, which is why the badge always accompanies the number.
export function daysUntil(dateIso: string, now: Date = new Date()): number {
  const [y, m, d] = dateIso.split("-").map(Number);
  const due = new Date(y ?? 0, (m ?? 1) - 1, d ?? 1).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((due - today) / 86_400_000);
}
