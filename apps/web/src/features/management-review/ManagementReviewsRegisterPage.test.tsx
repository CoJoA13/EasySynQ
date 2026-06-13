import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useParams } from "react-router-dom";
import { expect, it, describe } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ManagementReviewsRegisterPage } from "./ManagementReviewsRegisterPage";

// Grant mgmtReview.create (the default /me/permissions handler returns an empty set).
function grantCreate() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "mgmtReview.create", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
}

describe("ManagementReviewsRegisterPage", () => {
  it("renders a row per review (Ref → anchor + a Draft state chip for non-Effective)", async () => {
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    // First content assertion waits — the card renders before the row resolves.
    await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
    const row = screen.getByText("MR-001").closest("tr")!;
    // Ref is an anchor to the detail route.
    const anchor = within(row).getByRole("link", { name: "MR-001" });
    expect(anchor).toHaveAttribute("href", "/management-reviews/mr-0001-0001-0001-000000000001");
    // Draft (non-Effective) carries the state chip.
    expect(within(row).getByLabelText("State: Draft")).toBeInTheDocument();
    expect(within(row).getByText("2026 Annual Management Review")).toBeInTheDocument();
    expect(within(row).getByText("2026 Annual")).toBeInTheDocument();
    expect(within(row).getByText("2026-06-12")).toBeInTheDocument();
  });

  it("hides the create button without mgmtReview.create", async () => {
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /new management review/i })).toBeNull();
  });

  it("shows the create button when mgmtReview.create is granted", async () => {
    grantCreate();
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
    expect(
      await screen.findByRole("button", { name: /new management review/i }),
    ).toBeInTheDocument();
  });

  it("shows a calm no-access panel on a 403", async () => {
    server.use(
      http.get("/api/v1/management-reviews", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() =>
      expect(screen.getByText(/don't have access to management reviews/i)).toBeInTheDocument(),
    );
  });

  it("shows a calm error (not an infinite loader) on a non-403 failure", async () => {
    server.use(
      http.get("/api/v1/management-reviews", () =>
        HttpResponse.json({ code: "internal_error", title: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() =>
      expect(screen.getByText(/couldn't load management reviews/i)).toBeInTheDocument(),
    );
  });

  it("empty state copy branches on the create capability", async () => {
    server.use(
      http.get("/api/v1/management-reviews", () => HttpResponse.json({ data: [] })),
    );
    // Without create: the read-only copy.
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() =>
      expect(screen.getByText(/no management reviews have been convened yet/i)).toBeInTheDocument(),
    );
  });

  it("empty state invites convening the first review when create is granted", async () => {
    grantCreate();
    server.use(
      http.get("/api/v1/management-reviews", () => HttpResponse.json({ data: [] })),
    );
    renderWithProviders(<ManagementReviewsRegisterPage />, { route: "/management-reviews" });
    await waitFor(() =>
      expect(screen.getByText(/convene the first management review/i)).toBeInTheDocument(),
    );
  });

  // Drives NewManagementReviewModal end-to-end: open → type a title → submit → POST fires →
  // navigation to the new detail route happens (asserted via a sentinel detail route).
  it("create flow POSTs and navigates to the new review's detail route", async () => {
    grantCreate();
    let posted = false;
    server.use(
      http.post("/api/v1/management-reviews", async () => {
        posted = true;
        return HttpResponse.json(
          {
            id: "mr-0001-0001-0001-000000000001",
            identifier: "MR-001",
            title: "Q3 Review",
            current_state: "Draft",
            period_label: null,
            review_date: null,
            attendees: null,
            close_state: null,
            closed_at: null,
            created_at: "2026-06-13T09:00:00+00:00",
          },
          { status: 201 },
        );
      }),
    );

    function DetailStub() {
      const { id } = useParams();
      return <div>detail route: {id}</div>;
    }

    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/management-reviews" element={<ManagementReviewsRegisterPage />} />
        <Route path="/management-reviews/:id" element={<DetailStub />} />
      </Routes>,
      { route: "/management-reviews" },
    );

    await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
    await user.click(await screen.findByRole("button", { name: /new management review/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/^title/i), "Q3 Review");
    await user.click(within(dialog).getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(
        screen.getByText("detail route: mr-0001-0001-0001-000000000001"),
      ).toBeInTheDocument(),
    );
    expect(posted).toBe(true);
  });
});
