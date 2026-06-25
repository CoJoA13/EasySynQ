import {
  Badge,
  Button,
  Checkbox,
  Group,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState, MutationErrorState } from "../lib/states";
import type { WorkingCalendar, WorkingCalendarUpdate } from "../lib/types";
import { allTimeZones, detectTimeZone, restingZones } from "../features/notifications/timezones";
import { useUpdateWorkingCalendar, useWorkingCalendar } from "./hooks";

const WEEKDAYS: { iso: number; label: string }[] = [
  { iso: 1, label: "Monday" },
  { iso: 2, label: "Tuesday" },
  { iso: 3, label: "Wednesday" },
  { iso: 4, label: "Thursday" },
  { iso: 5, label: "Friday" },
  { iso: 6, label: "Saturday" },
  { iso: 7, label: "Sunday" },
];

interface Working {
  name: string;
  days: number[]; // sorted-unique ISO ints
  holidays: string[]; // sorted-unique YYYY-MM-DD
  timezone: string;
}

const uniqSortNums = (xs: number[]) => [...new Set(xs)].sort((a, b) => a - b);
const uniqSortStrs = (xs: string[]) => [...new Set(xs)].sort();

function toWorking(c: WorkingCalendar): Working {
  return {
    name: c.name,
    days: uniqSortNums(c.working_days),
    holidays: uniqSortStrs(c.holidays),
    timezone: c.timezone,
  };
}

// Value-equality over canonical (already sorted-unique) forms — reference !== would read permanently
// dirty after the post-save reseed (the S-notify-3b post-save-reset class).
function isDirty(w: Working, b: WorkingCalendar): boolean {
  return (
    w.name !== b.name ||
    w.timezone !== b.timezone ||
    JSON.stringify(w.days) !== JSON.stringify(uniqSortNums(b.working_days)) ||
    JSON.stringify(w.holidays) !== JSON.stringify(uniqSortStrs(b.holidays))
  );
}

export function WorkingCalendarEditor() {
  const cal = useWorkingCalendar();
  const update = useUpdateWorkingCalendar();
  const [working, setWorking] = useState<Working | null>(null);
  const [holidayInput, setHolidayInput] = useState("");

  useEffect(() => {
    if (cal.data) setWorking(toWorking(cal.data));
  }, [cal.data]);

  const detected = useMemo(detectTimeZone, []);
  const [tzSearch, setTzSearch] = useState("");
  const tzData = useMemo(() => {
    const current = working?.timezone ?? "UTC";
    const q = tzSearch.trim().toLowerCase();
    if (!q) return restingZones(detected, current);
    const matches = allTimeZones()
      .filter((z) => z.toLowerCase().includes(q))
      .slice(0, 50);
    return matches.includes(current) ? matches : [current, ...matches];
  }, [tzSearch, detected, working?.timezone]);

  if (cal.isError) {
    return (
      <ErrorState title="Couldn't load the working calendar" onRetry={() => void cal.refetch()} />
    );
  }
  if (cal.isLoading || !working || !cal.data) {
    return <LoadingState label="Loading working calendar" />;
  }

  const trimmedName = working.name.trim();
  const nameError =
    trimmedName.length === 0
      ? "A name is required"
      : trimmedName.length > 255
        ? "Name must be 255 characters or fewer"
        : undefined;
  const validName = nameError === undefined;
  const hasDay = working.days.length > 0;
  const dirty = isDirty(working, cal.data);
  const canSave = (dirty || !cal.data.exists) && validName && hasDay;

  const addHoliday = () => {
    const v = holidayInput; // <input type=date> emits "" when empty/invalid
    if (!v || working.holidays.includes(v)) return;
    setWorking({ ...working, holidays: uniqSortStrs([...working.holidays, v]) });
    setHolidayInput("");
  };

  const removeHoliday = (d: string) =>
    setWorking({ ...working, holidays: working.holidays.filter((h) => h !== d) });

  const save = () => {
    if (!canSave) return;
    const body: WorkingCalendarUpdate = {
      name: working.name.trim(),
      working_days: working.days,
      holidays: working.holidays,
      timezone: working.timezone,
    };
    update.mutate(body);
  };

  return (
    <Stack gap="md">
      <Title order={3}>Working calendar</Title>
      <Text size="sm" c="dimmed">
        Business-day reminders and escalations skip non-working days and the holidays below.
      </Text>

      <TextInput
        label="Calendar name"
        value={working.name}
        error={nameError}
        onChange={(e) => setWorking({ ...working, name: e.currentTarget.value })}
      />

      <Checkbox.Group
        label="Working days"
        description="The weekdays SLAs count as business days."
        value={working.days.map(String)}
        onChange={(vals) => setWorking({ ...working, days: uniqSortNums(vals.map(Number)) })}
        error={!hasDay ? "Select at least one working day" : undefined}
      >
        <Group gap="md" mt="xs">
          {WEEKDAYS.map((d) => (
            <Checkbox key={d.iso} value={String(d.iso)} label={d.label} aria-label={d.label} />
          ))}
        </Group>
      </Checkbox.Group>

      <Stack gap="xs">
        <Text fw={600} size="sm">
          Holidays
        </Text>
        <Group align="flex-end">
          <TextInput
            type="date"
            label="Holiday date"
            aria-label="Holiday date"
            value={holidayInput}
            onChange={(e) => setHolidayInput(e.currentTarget.value)}
          />
          <Button
            variant="default"
            onClick={addHoliday}
            disabled={!holidayInput}
            aria-label="Add holiday"
          >
            Add holiday
          </Button>
        </Group>
        <Group gap="xs">
          {working.holidays.length === 0 ? (
            <Text size="sm" c="dimmed">
              No holidays.
            </Text>
          ) : (
            working.holidays.map((d) => (
              <Badge
                key={d}
                variant="light"
                rightSection={
                  <Button
                    variant="transparent"
                    size="compact-xs"
                    p={0}
                    aria-label={`Remove holiday ${d}`}
                    onClick={() => removeHoliday(d)}
                  >
                    ×
                  </Button>
                }
              >
                {d}
              </Badge>
            ))
          )}
        </Group>
      </Stack>

      <Select
        label="Timezone"
        description="Business days are evaluated in this timezone. Type to search all time zones."
        searchable
        searchValue={tzSearch}
        onSearchChange={setTzSearch}
        onDropdownOpen={() => setTzSearch("")}
        data={tzData}
        value={working.timezone}
        onChange={(v) => v && setWorking({ ...working, timezone: v })}
        nothingFoundMessage="No matching zone"
        limit={50}
        allowDeselect={false}
        comboboxProps={{ keepMounted: false }}
      />

      <div>
        <Button onClick={save} disabled={!canSave} loading={update.isPending}>
          Save calendar
        </Button>
        {update.isSuccess && !dirty && (
          <Text size="sm" c="dimmed" mt="xs">
            Saved.
          </Text>
        )}
      </div>
      {update.isError && (
        <MutationErrorState title="Couldn't save the working calendar" error={update.error} />
      )}
    </Stack>
  );
}
