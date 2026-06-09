import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// The Internal-Audit front door's secondary nav (S-web-7d): Audits (index) · Programme.
// The /audits/:id detail page sits OUTSIDE this layout (it is a destination, not a tab).
const TABS = [
  { value: "audits", label: "Audits", path: "/audits" },
  { value: "programme", label: "Programme", path: "/audits/programme" },
] as const;

function activeTab(pathname: string): string {
  return pathname.startsWith("/audits/programme") ? "programme" : "audits";
}

export function AuditsLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Container size="xl" pt="md" pb={0}>
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
