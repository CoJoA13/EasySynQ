import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { IngestionRunsPage } from "./IngestionRunsPage";

const RID = ingestionRunFixture.id;

// Grant import.execute (the default /me/permissions returns []), so the "New import" button shows.
function grantExecute() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.execute", effect: "ALLOW" }],
      }),
    ),
  );
}

// Render the page under a sentinel route so a navigate('/ingestion/<id>') is observable.
function renderPage(route = "/ingestion") {
  return renderWithProviders(
    <Routes>
      <Route path="/ingestion" element={<IngestionRunsPage />} />
      <Route path="/ingestion/:runId" element={<div>RUN PAGE</div>} />
    </Routes>,
    { route },
  );
}

test("renders the fixture run with its source_root, a status badge, and a link to /ingestion/<id>", async () => {
  renderPage();
  const link = await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(link).toHaveAttribute("href", `/ingestion/${RID}`);
  expect(screen.getByLabelText("Run status: Proposed")).toBeInTheDocument();
});

test('shows the "New import" button only when can("import.execute")', async () => {
  // Default permissions ([]) → no button.
  renderPage();
  await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(screen.queryByRole("button", { name: /New import/ })).not.toBeInTheDocument();
});

test('clicking "New import" opens the NewImportModal (the source root field appears)', async () => {
  grantExecute();
  renderPage();
  const button = await screen.findByRole("button", { name: /New import/ });
  await userEvent.click(button);
  expect(await screen.findByLabelText(/Source folder path/i)).toBeInTheDocument();
});

test("an empty run list shows the calm empty state", async () => {
  server.use(http.get("/api/v1/admin/imports", () => HttpResponse.json([])));
  renderPage();
  expect(await screen.findByText("No imports yet.")).toBeInTheDocument();
});

test("a 403 renders the calm no-access panel (no red error)", async () => {
  server.use(
    http.get("/api/v1/admin/imports", () =>
      HttpResponse.json({ code: "forbidden", detail: "no access" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to import review.")).toBeInTheDocument();
});

test("has no axe violations (list, empty, and no-access)", async () => {
  const list = renderPage();
  await screen.findByRole("link", { name: /legacy-qms-share/ });
  expect(await axe(list.container)).toHaveNoViolations();
  list.unmount();

  server.use(http.get("/api/v1/admin/imports", () => HttpResponse.json([])));
  const empty = renderPage();
  await screen.findByText("No imports yet.");
  expect(await axe(empty.container)).toHaveNoViolations();
  empty.unmount();

  server.use(
    http.get("/api/v1/admin/imports", () =>
      HttpResponse.json({ code: "forbidden", detail: "no access" }, { status: 403 }),
    ),
  );
  const denied = renderPage();
  await screen.findByText("You don't have access to import review.");
  await waitFor(() => expect(denied.container.querySelector("table")).not.toBeInTheDocument());
  expect(await axe(denied.container)).toHaveNoViolations();
});
