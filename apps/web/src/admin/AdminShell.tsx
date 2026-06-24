import { Button, Container, Group, Stack, Tabs, Title } from "@mantine/core";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";

// S8d: the post-finalize admin shell — a sub-nav over the Users & Roles surfaces. The QMS-content
// shells (process map, library) + the deferred wizard steps land in later slices; this is the seam.
export function AdminShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const tab = pathname.includes("/admin/roles")
    ? "roles"
    : pathname.includes("/admin/processes")
      ? "processes"
      : pathname.includes("/admin/config")
        ? "config"
        : "users";
  return (
    <Container size="lg" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>Administration</Title>
          <Button component={Link} to="/" variant="subtle">
            Back to app
          </Button>
        </Group>
        <Tabs value={tab} onChange={(v) => navigate(`/admin/${v ?? "users"}`)}>
          <Tabs.List>
            <Tabs.Tab value="users">Users</Tabs.Tab>
            <Tabs.Tab value="roles">Roles</Tabs.Tab>
            <Tabs.Tab value="processes">Processes</Tabs.Tab>
            <Tabs.Tab value="config">Config</Tabs.Tab>
          </Tabs.List>
        </Tabs>
        <Outlet />
      </Stack>
    </Container>
  );
}
