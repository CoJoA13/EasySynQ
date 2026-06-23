// Timezone helpers for the daily-digest scheduling control (S-notify-3b). The FE offers a calm curated
// resting list and a searchable full IANA set sourced from Intl. Intl's zone set (~418 canonical CLDR
// zones) is a SUBSET of the server's zoneinfo.available_timezones() (~600), so every zone offered here is
// server-valid → no invalid_timezone 422.

// ~12 common zones shown at rest (calm default). All are canonical IANA names present in zoneinfo.
export const COMMON_ZONES: string[] = [
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Asia/Kolkata",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Australia/Sydney",
];

/** The browser's current IANA zone (e.g. "America/New_York"); "UTC" if unavailable. */
export function detectTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** The full IANA zone list (Intl-sourced), merged with COMMON_ZONES + sorted; falls back to
 *  COMMON_ZONES if Intl.supportedValuesOf is unavailable in the runtime. */
export function allTimeZones(): string[] {
  let supported: string[] = [];
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] })
      .supportedValuesOf;
    if (fn) supported = fn("timeZone");
  } catch {
    supported = [];
  }
  return [...new Set<string>([...COMMON_ZONES, ...supported])].sort();
}

/** The calm resting list for the Select: COMMON_ZONES with the currently-stored zone then the detected
 *  zone prepended (deduped) so both are one click away. */
export function restingZones(detected: string, current: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const z of [current, detected, ...COMMON_ZONES]) {
    if (z && !seen.has(z)) {
      seen.add(z);
      out.push(z);
    }
  }
  return out;
}
