import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LeftRail } from "./LeftRail";

test("LeftRail shows Home/Library nav + PDCA clause groups", async () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Library" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("PLAN")).toBeInTheDocument());
  expect(screen.getByText("DO")).toBeInTheDocument();
  expect(screen.getByText("CHECK")).toBeInTheDocument();
  expect(screen.getByText("ACT")).toBeInTheDocument();
});

test("LeftRail shows the Review & Approve nav link", () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  expect(screen.getByRole("link", { name: "Review & Approve" })).toHaveAttribute("href", "/tasks");
});

test("the Nonconformity & CAPA entry is always shown (discoverable; page handles 403)", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Nonconformity & CAPA")).toBeInTheDocument();
});

test("hides the Compliance entry when the caller lacks report.compliance_checklist.read", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  await screen.findByText("Library");
  expect(screen.queryByText("Compliance")).not.toBeInTheDocument();
});

test("shows the gated Compliance entry when the caller holds the key", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "report.compliance_checklist.read", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Compliance")).toBeInTheDocument();
});

test("hides the Import entry when the caller lacks import.review", async () => {
  // default MSW /me/permissions returns no key → the admin-only Import entry is hidden
  renderWithProviders(<LeftRail />, { route: "/" });
  await screen.findByText("Library");
  expect(screen.queryByText("Import")).not.toBeInTheDocument();
});

test("shows the gated Import entry when the caller holds import.review", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.review", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/ingestion" });
  const link = await screen.findByRole("link", { name: "Import" });
  expect(link).toHaveAttribute("href", "/ingestion");
});

test("Internal Audit entry is unconditional (the CAPA precedent — calm-403 lives on the page)", async () => {
  renderWithProviders(<LeftRail />);
  expect(await screen.findByRole("link", { name: "Internal Audit" })).toHaveAttribute(
    "href",
    "/audits",
  );
});
