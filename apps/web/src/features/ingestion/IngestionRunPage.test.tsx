import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { IngestionRunPage } from "./IngestionRunPage";

const RID = ingestionRunFixture.id;

function renderPage(route = `/ingestion/${RID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="ingestion/:runId" element={<IngestionRunPage />} />
    </Routes>,
    { route },
  );
}

test("IngestionRunPage shows a loader before the run resolves", () => {
  renderPage();
  expect(screen.getByLabelText("Loading import run")).toBeInTheDocument();
});

test("IngestionRunPage renders the review cockpit for a Proposed run", async () => {
  renderPage();
  // a cockpit-only affordance: the queue tablist (QueueTabs, Task 7)
  expect(await screen.findByRole("tab", { name: /Needs decision/ })).toBeInTheDocument();
});

test("IngestionRunPage renders the commit-progress face for a Committing run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Committing to the vault/)).toBeInTheDocument();
});

test("IngestionRunPage renders the scan-progress face for a pre-Proposed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Scanning" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Scanning the source/)).toBeInTheDocument();
});

test("an unknown/additive status degrades calmly to the scan-progress face (invariant 6)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "SomeFutureStage" }),
    ),
  );
  renderPage();
  // the default switch branch routes an unknown status to ScanProgress, which shows the generic
  // "Working…" caption (Task 16 fix) rather than crashing or going blank.
  expect(await screen.findByText(/Working…/)).toBeInTheDocument();
});

test("IngestionRunPage renders the terminal summary for a Completed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Completed" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Import complete/)).toBeInTheDocument();
});

test("IngestionRunPage shows a calm not-found panel on a 404", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("Import run not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to imports/ })).toBeInTheDocument();
});

test("IngestionRunPage shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to import review.")).toBeInTheDocument();
});

test("IngestionRunPage has no a11y violations (cockpit)", async () => {
  const { container } = renderPage();
  await screen.findByRole("tab", { name: /Needs decision/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("IngestionRunPage has no a11y violations (404)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  const { container } = renderPage();
  await screen.findByText("Import run not found.");
  expect(await axe(container)).toHaveNoViolations();
});
