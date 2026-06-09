import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import type { Finding } from "../../lib/types";
import { FindingPanel } from "./FindingPanel";

function r(ui: React.ReactElement) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>{ui}</MemoryRouter>
    </MantineProvider>,
  );
}

const nc: Finding = {
  id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062",
  title: "Supplier re-evaluation overdue for 2 vendors",
  audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major",
  clause_ref: "8.4", process_ref: "Purchasing",
  auto_capa_id: "ca000001-0001-0001-0001-000000000001",
  correction_of: null, superseded_by_correction: null,
};

test("a live NC renders badge + title + tags + the CAPA state chip + the deep-link", () => {
  r(<FindingPanel finding={nc} capaState="RootCause" canCorrect onCorrect={() => {}} />);
  expect(screen.getByText("REC-000062")).toBeInTheDocument();
  expect(screen.getByText(/⚑ Major NC/)).toBeInTheDocument();
  expect(screen.getByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  expect(screen.getByText("8.4")).toBeInTheDocument();
  expect(screen.getByText(/CAPA: Root cause/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View CAPA/ })).toHaveAttribute(
    "href",
    "/capa?capa=ca000001-0001-0001-0001-000000000001",
  );
});

test("the CAPA chip is omitted when capaState is undefined (capa.read degrade) — the link stays", () => {
  r(<FindingPanel finding={nc} capaState={undefined} canCorrect={false} onCorrect={() => {}} />);
  expect(screen.queryByText(/CAPA:/)).toBeNull();
  expect(screen.getByRole("link", { name: /View CAPA/ })).toBeInTheDocument();
});

test("a superseded finding renders muted with no Correct action", () => {
  r(
    <FindingPanel
      finding={{ ...nc, superseded_by_correction: "fd000004-0004-0004-0004-000000000004" }}
      capaState="Closed"
      canCorrect
      onCorrect={() => {}}
    />,
  );
  expect(screen.getByText(/Superseded by correction/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Correct/ })).toBeNull();
});

test("a successor shows its corrects-link; Correct fires the callback when allowed", async () => {
  const onCorrect = vi.fn();
  const u = userEvent.setup();
  r(
    <FindingPanel
      finding={{ ...nc, finding_type: "OBSERVATION", severity: null, auto_capa_id: null, correction_of: "fd000003-0003-0003-0003-000000000003" }}
      capaState={undefined}
      canCorrect
      onCorrect={onCorrect}
    />,
  );
  expect(screen.getByText(/Corrects an earlier finding/)).toBeInTheDocument();
  await u.click(screen.getByRole("button", { name: /Correct/ }));
  expect(onCorrect).toHaveBeenCalled();
});

test("a finding title with markup renders as literal text (XSS-safe)", () => {
  r(
    <FindingPanel
      finding={{ ...nc, title: "<img src=x onerror=alert(1)>" }}
      capaState={undefined}
      canCorrect={false}
      onCorrect={() => {}}
    />,
  );
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
});
