import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ObjectiveDetailPage } from "./ObjectiveDetailPage";

const ID = "ob000001-0001-0001-0001-000000000001";

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/objectives/:id" element={<ObjectiveDetailPage />} />
    </Routes>,
    { route: `/objectives/${id}` },
  );
}

it("renders the header, commitment, plans and measurements", async () => {
  const { container } = renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByRole("heading", { name: "On-time delivery rate" })).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();
  expect(screen.getByText("Add a second carrier to the south region")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("2026-04-01")).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a not-found alert on a 404", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Objective not found" }, { status: 404 }),
    ),
  );
  renderAt(ID);
  await waitFor(() => expect(screen.getByText(/couldn't load this objective/i)).toBeInTheDocument());
});
