import { Group, SegmentedControl, Stack, Text, Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";
import { formatOrgClock } from "../../lib/time";
import { type ColorSchemePreference, useColorSchemePreference } from "./useColorSchemePreference";
import { useMe } from "./useMe";

const SCHEME_OPTIONS: { value: ColorSchemePreference; label: string }[] = [
  { value: "LIGHT", label: "Light" },
  { value: "DARK", label: "Dark" },
  { value: "AUTO", label: "Auto" },
];

// A minute-precision clock only needs to re-render when the displayed minute can have changed.
// Ticking every second would re-render the whole shell 60× more often for no visible difference.
const TICK_MS = 15_000;

/**
 * R69's rail foot: the interface colour-scheme control, and a clock on ORGANIZATION time.
 *
 * The clock is deliberately not browser-local. Records and audit events are stamped in org time and
 * `useOrgDate` (U20, the C11 class) already renders every register and timeline date that way, so a
 * browser-local clock in the same frame would disagree with the content directly above it for any
 * user east or west of the organization. It is labelled with the zone's own short name so it can
 * never be mistaken for local time.
 */
export function RailFoot() {
  const { data: me } = useMe();
  const { value, select } = useColorSchemePreference();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const clock = formatOrgClock(now, me?.org_timezone);

  return (
    <Stack gap="xs" p="sm">
      {/* The clock sits ABOVE the theme control: it is the thing a reader glances at, while the
          control is acted on rarely, so the frequently-read value takes the position nearer the
          nav it belongs beside. Both stay outside the nav's scroll area. */}
      {clock ? (
        <Tooltip label={`Organization date and time (${clock.zone})`} position="top" withArrow>
          <Group gap={6} justify="center" wrap="nowrap">
            <Text size="xs" c="dimmed" aria-label="Organization date">
              {clock.date}
            </Text>
            <Text size="xs" c="dimmed" fw={500} aria-label="Organization time">
              {clock.time}
            </Text>
            <Text size="xs" c="dimmed">
              {clock.zone}
            </Text>
          </Group>
        </Tooltip>
      ) : null}
      <SegmentedControl
        size="xs"
        fullWidth
        // The LIVE preference, not the account value — see useColorSchemePreference. Binding this
        // to `/me` showed AUTO over an already-light page both before /me resolved and after a
        // failed write.
        value={value}
        onChange={(value) => select(value as ColorSchemePreference)}
        // Deliberately NOT `disabled` while the write is in flight. Disabling the element that
        // currently holds focus drops focus to <body>, so a keyboard user loses their place on every
        // theme change — and the control has nothing to protect: the scheme is applied locally and
        // optimistically, a second choice simply supersedes the first, and a failed write reports
        // itself. The only thing disabling bought was a focus bug.
        data={SCHEME_OPTIONS}
        aria-label="Interface theme"
      />
    </Stack>
  );
}
