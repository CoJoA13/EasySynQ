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
