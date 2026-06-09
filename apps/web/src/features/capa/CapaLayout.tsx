import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// The Nonconformity & CAPA front door's secondary nav (S-web-7c). The board lives at the index route
// and is UNCHANGED — this layout only adds the tab strip + <Outlet/>. No <Title> here, so each face
// (incl. the byte-identical CapaBoardPage) keeps its own.
const TABS = [
  { value: "board", label: "Board", path: "/capa" },
  { value: "complaints", label: "Complaints", path: "/capa/complaints" },
  { value: "ncrs", label: "NCRs", path: "/capa/ncrs" },
] as const;

function activeTab(pathname: string): string {
  if (pathname.startsWith("/capa/complaints")) return "complaints";
  if (pathname.startsWith("/capa/ncrs")) return "ncrs";
  return "board";
}

export function CapaLayout() {
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
