import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { LeftRail } from "../../app/shell/LeftRail";
import { DriftLayout } from "./DriftLayout";

function renderAt(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/drift" element={<DriftLayout />}>
        <Route index element={<div>STATUS-FACE</div>} />
        <Route path="superseded-copies" element={<div>D4-FACE</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

describe("DriftLayout", () => {
  test("index route shows the Status tab content", () => {
    renderAt("/drift");
    expect(screen.getByText("STATUS-FACE")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Superseded copies" })).toBeInTheDocument();
  });
  test("superseded-copies route shows the D4 tab content", () => {
    renderAt("/drift/superseded-copies");
    expect(screen.getByText("D4-FACE")).toBeInTheDocument();
  });
});

describe("LeftRail drift gating", () => {
  test("no drift.read → no Drift entry", async () => {
    renderWithProviders(<LeftRail />);
    expect(await screen.findByText("Library")).toBeInTheDocument();
    expect(screen.queryByText("Drift")).not.toBeInTheDocument();
  });
  test("drift.read → the Drift entry renders", async () => {
    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: [{ key: "drift.read", effect: "ALLOW" }],
        }),
      ),
    );
    renderWithProviders(<LeftRail />);
    expect(await screen.findByText("Drift")).toBeInTheDocument();
  });
});
