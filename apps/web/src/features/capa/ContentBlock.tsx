import { List, Stack, Text } from "@mantine/core";

// Free-form per-stage content_block (no fixed v1 schema). Render generically as labeled key/value —
// React escapes all interpolated strings, so an HTML-looking value is shown as literal text (never
// dangerouslySetInnerHTML). Humanize snake_case keys for display only.
function humanize(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  return s.length ? s[0]!.toUpperCase() + s.slice(1) : key;
}

function renderValue(value: unknown) {
  if (Array.isArray(value)) {
    return (
      <List size="sm" withPadding>
        {value.map((v, i) => (
          <List.Item key={i}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</List.Item>
        ))}
      </List>
    );
  }
  if (value !== null && typeof value === "object") {
    return <Text size="sm">{JSON.stringify(value)}</Text>;
  }
  return <Text size="sm">{String(value)}</Text>;
}

export function ContentBlock({ block }: { block: Record<string, unknown> }) {
  const entries = Object.entries(block ?? {});
  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No details recorded.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {entries.map(([key, value]) => (
        <div key={key}>
          <Text size="xs" fw={600} c="dimmed">
            {humanize(key)}
          </Text>
          {renderValue(value)}
        </div>
      ))}
    </Stack>
  );
}
