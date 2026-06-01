import { Badge, Card, Container, Group, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

interface Dependency {
  name: string;
  ready: boolean;
  detail: string | null;
}
interface Readiness {
  ready: boolean;
  dependencies: Dependency[];
}

async function fetchReadiness(): Promise<Readiness> {
  const resp = await fetch("/readyz");
  return (await resp.json()) as Readiness;
}

export function App() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["readyz"],
    queryFn: fetchReadiness,
    refetchInterval: 5000,
  });

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Title order={1}>EasySynQ</Title>
        <Text c="dimmed">
          Self-hosted ISO 9001:2015 QMS — walking skeleton (slice S0). The controlled vault,
          lifecycle, and audit trail land in subsequent slices.
        </Text>

        <Card withBorder radius="md" padding="lg" className="border-state-draft">
          <Group justify="space-between" mb="sm">
            <Text fw={600}>System readiness</Text>
            {isLoading ? (
              <Badge color="gray">checking…</Badge>
            ) : isError ? (
              <Badge color="red">unreachable</Badge>
            ) : (
              <Badge color={data?.ready ? "green" : "yellow"}>
                {data?.ready ? "ready" : "degraded"}
              </Badge>
            )}
          </Group>

          <Stack gap="xs">
            {data?.dependencies.map((dep) => (
              <Group key={dep.name} justify="space-between">
                <Text size="sm">{dep.name}</Text>
                <Badge size="sm" color={dep.ready ? "green" : "red"} variant="light">
                  {dep.ready ? "ok" : (dep.detail ?? "down")}
                </Badge>
              </Group>
            ))}
          </Stack>
        </Card>
      </Stack>
    </Container>
  );
}
