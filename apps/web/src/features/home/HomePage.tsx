import { Card, Stack, Text, Title } from "@mantine/core";

export function HomePage() {
  return (
    <Stack gap="md">
      <Title order={1}>QMS Health</Title>
      <Card withBorder radius="md" padding="lg">
        <Text fw={600} mb="xs">Welcome to EasySynQ</Text>
        <Text c="dimmed" size="sm">
          The PDCA dashboard lands in a later slice. For now, head to the Library to browse
          controlled documents.
        </Text>
      </Card>
    </Stack>
  );
}
