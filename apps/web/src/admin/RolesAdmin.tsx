import { Accordion, Alert, Badge, Group, Loader, Stack, Table, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";

interface Role {
  id: string;
  name: string;
  description: string | null;
  is_reserved: boolean;
}
interface RoleDetail extends Role {
  grants: { permission_key: string; scope_template: Record<string, unknown> | null }[];
}

// S8d: read-only view of the 8 seeded role bundles + their grants (GET /roles, GET /roles/{id}).
// Custom-role authoring (create/edit/delete) is a v1 surface — deliberately not here.
export function RolesAdmin({ token }: { token: string | null }) {
  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: () => apiGet<Role[]>("/api/v1/roles", token),
    enabled: !!token,
  });

  if (roles.isLoading) return <Loader />;
  if (roles.isError)
    return <Alert color="red" title="Could not load roles">{String(roles.error)}</Alert>;

  return (
    <Stack gap="md">
      <Text c="dimmed" size="sm">
        The seeded role bundles. Assign them to users on the Users tab. Custom roles arrive in a later
        release.
      </Text>
      <Accordion variant="separated">
        {(roles.data ?? []).map((r) => (
          <RoleRow key={r.id} role={r} token={token} />
        ))}
      </Accordion>
    </Stack>
  );
}

function RoleRow({ role, token }: { role: Role; token: string | null }) {
  const detail = useQuery({
    queryKey: ["role", role.id],
    queryFn: () => apiGet<RoleDetail>(`/api/v1/roles/${role.id}`, token),
    enabled: !!token,
  });
  return (
    <Accordion.Item value={role.id}>
      <Accordion.Control>
        <Group gap="xs">
          <Text fw={600}>{role.name}</Text>
          {role.is_reserved && (
            <Badge size="xs" variant="light" color="gray">
              reserved
            </Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed">
          {role.description}
        </Text>
      </Accordion.Control>
      <Accordion.Panel>
        {detail.isLoading ? (
          <Loader size="sm" />
        ) : (
          <Table withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Permission</Table.Th>
                <Table.Th>Scope</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(detail.data?.grants ?? []).map((g) => (
                <Table.Tr key={g.permission_key}>
                  <Table.Td>
                    <Text size="sm" ff="monospace">
                      {g.permission_key}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {(g.scope_template as { level?: string } | null)?.level ?? "—"}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Accordion.Panel>
    </Accordion.Item>
  );
}
