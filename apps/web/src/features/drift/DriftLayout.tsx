import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// S-web-8: the drift surface's secondary nav. Both faces are drift.read-gated server-side; the
// layout itself renders for anyone (each page shows its own calm-403).
const TABS = [
  { value: "status", label: "Status", path: "/drift" },
  { value: "superseded", label: "Superseded copies", path: "/drift/superseded-copies" },
] as const;

function activeTab(pathname: string): string {
  return pathname.startsWith("/drift/superseded-copies") ? "superseded" : "status";
}

export function DriftLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Container size="lg" pt="md" pb={0}>
        <Tabs
          value={activeTab(pathname)}
          onChange={(v) => {
            const tab = TABS.find((t) => t.value === v);
            if (tab) navigate(tab.path);
          }}
        >
          <Tabs.List>
            {TABS.map((t) => (
              <Tabs.Tab key={t.value} value={t.value}>
                {t.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
      </Container>
      <Outlet />
    </>
  );
}
