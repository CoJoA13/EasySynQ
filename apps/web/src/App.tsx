import { Badge, Button, Card, Container, Group, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "./lib/auth";

interface Dependency {
  name: string;
  ready: boolean;
  detail: string | null;
}
interface Readiness {
  ready: boolean;
  dependencies: Dependency[];
}
interface Me {
  id: string;
  display_name: string | null;
  email: string | null;
  status: string;
}

async function fetchReadiness(): Promise<Readiness> {
  return (await (await fetch("/readyz")).json()) as Readiness;
}

async function fetchMe(token: string): Promise<Me> {
  const resp = await fetch("/api/v1/me", { headers: { Authorization: `Bearer ${token}` } });
  if (!resp.ok) throw new Error(`/me ${resp.status}`);
  return (await resp.json()) as Me;
}

function ReadinessCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["readyz"],
    queryFn: fetchReadiness,
    refetchInterval: 5000,
  });
  return (
    <Card withBorder radius="md" padding="lg">
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
  );
}

function AccountCard({ token }: { token: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["me", token],
    queryFn: () => fetchMe(token),
  });
  return (
    <Card withBorder radius="md" padding="lg">
      <Text fw={600} mb="sm">
        Signed in
      </Text>
      {isLoading ? (
        <Text size="sm" c="dimmed">
          loading profile…
        </Text>
      ) : isError ? (
        <Text size="sm" c="red">
          could not load /me
        </Text>
      ) : (
        <Stack gap={4}>
          <Text size="sm">{data?.display_name ?? "(no name)"}</Text>
          <Text size="sm" c="dimmed">
            {data?.email ?? "(no email)"}
          </Text>
          <Badge size="sm" variant="light" color="teal">
            {data?.status}
          </Badge>
        </Stack>
      )}
    </Card>
  );
}

export function App() {
  const { ready, user, login, logout } = useAuth();
  const token = user?.access_token ?? null;

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>EasySynQ</Title>
          {ready &&
            (token ? (
              <Button variant="light" onClick={logout}>
                Sign out
              </Button>
            ) : (
              <Button onClick={login}>Sign in</Button>
            ))}
        </Group>
        <Text c="dimmed">
          Self-hosted ISO 9001:2015 QMS — slices S0 (walking skeleton) + S1 (authentication).
        </Text>

        {token && <AccountCard token={token} />}
        <ReadinessCard />
      </Stack>
    </Container>
  );
}
