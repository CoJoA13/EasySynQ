// Presentational register-triage building blocks (critique #5): the search/filter toolbar, the
// sortable column header, and the subject (identifier + short title) cell. Paired with the hooks in
// ./registerControls and the row-nav hook in ./useRowKeyboardNav.

import { Group, Table, Text, TextInput, UnstyledButton } from "@mantine/core";
import type { ComponentProps, ReactNode } from "react";
import { IconChevronDown, IconChevronSort, IconChevronUp, IconSearch } from "./icons";
import type { SortDir } from "./registerControls";

/** The toolbar above a register table: a debounced search box (wired to useDebouncedSearch), an
 *  optional slot for enum filters (Selects/SegmentedControls), and an optional result count. */
export function RegisterToolbar({
  q,
  onQ,
  placeholder,
  count,
  countNoun = "shown",
  searchWidth = 260,
  children,
}: {
  q: string;
  onQ: (v: string) => void;
  placeholder?: string;
  count?: number;
  countNoun?: string;
  searchWidth?: number;
  children?: ReactNode;
}) {
  return (
    <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
      <Group align="flex-end" wrap="wrap" gap="sm">
        <TextInput
          value={q}
          onChange={(e) => onQ(e.currentTarget.value)}
          placeholder={placeholder ?? "Search…"}
          aria-label="Search"
          leftSection={<IconSearch size={16} />}
          w={searchWidth}
        />
        {children}
      </Group>
      {count !== undefined && (
        <Text size="sm" c="dimmed" aria-live="polite">
          {count} {countNoun}
        </Text>
      )}
    </Group>
  );
}

/** A sortable column header. `aria-sort` reflects the active sort; the button announces the next
 *  action; a chevron shows direction (an inactive sortable column gets the dimmed double-chevron). */
export function SortableTh<K extends string>({
  label,
  sortKey,
  sort,
  dir,
  onSort,
  ...thProps
}: {
  label: string;
  sortKey: K;
  sort: K | null;
  dir: SortDir;
  onSort: (k: K) => void;
} & Omit<ComponentProps<typeof Table.Th>, "children">) {
  const active = sort === sortKey;
  return (
    <Table.Th
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      {...thProps}
    >
      <UnstyledButton
        onClick={() => onSort(sortKey)}
        aria-label={`Sort by ${label}`}
        style={{ display: "inline-flex", alignItems: "center", gap: 4, font: "inherit" }}
      >
        <span>{label}</span>
        {active ? (
          dir === "asc" ? (
            <IconChevronUp size={14} />
          ) : (
            <IconChevronDown size={14} />
          )
        ) : (
          <IconChevronSort size={14} style={{ opacity: 0.4 }} />
        )}
      </UnstyledButton>
    </Table.Th>
  );
}

/** The subject of a register row: a human identifier over a one-line-clamped short title. Falls back
 *  to a calm dash when neither is present (e.g. a CREATE DCR with no target, or an unresolved id). */
export function SubjectCell({
  identifier,
  title,
  fallback = "—",
}: {
  identifier?: string | null;
  title?: string | null;
  fallback?: ReactNode;
}) {
  if (!identifier && !title) return <Text c="dimmed">{fallback}</Text>;
  return (
    <div>
      {identifier && <Text fw={500}>{identifier}</Text>}
      {title && (
        <Text size="sm" c="dimmed" lineClamp={1}>
          {title}
        </Text>
      )}
    </div>
  );
}
