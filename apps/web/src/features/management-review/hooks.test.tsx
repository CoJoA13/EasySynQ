import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import {
  useMgmtReview,
  useMgmtReviewApproval,
  useMgmtReviewNextDue,
  useMgmtReviews,
} from "./hooks";

// ---- useMgmtReviews ----

function MgmtReviewsProbe() {
  const { data, forbidden } = useMgmtReviews();
  if (forbidden) return <div>forbidden</div>;
  return <div>{(data?.data ?? []).map((r) => r.identifier).join(",")}</div>;
}

describe("useMgmtReviews", () => {
  it("returns the list of management reviews", async () => {
    renderWithProviders(<MgmtReviewsProbe />);
    expect(await screen.findByText(/MR-001/)).toBeInTheDocument();
  });

  it("surfaces a forbidden flag on 403", async () => {
    server.use(
      http.get("/api/v1/management-reviews", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<MgmtReviewsProbe />);
    expect(await screen.findByText("forbidden")).toBeInTheDocument();
  });
});

// ---- useMgmtReview ----

function MgmtReviewDetailProbe({ id }: { id: string | null }) {
  const { data, forbidden } = useMgmtReview(id);
  if (forbidden) return <div>detail-forbidden</div>;
  if (!data) return <div>none</div>;
  const inputCount = data.inputs.length;
  const outputCount = data.outputs.length;
  return (
    <div>
      {data.title} inputs:{inputCount} outputs:{outputCount}
    </div>
  );
}

describe("useMgmtReview", () => {
  it("fetches the detail with inputs and outputs", async () => {
    renderWithProviders(
      <MgmtReviewDetailProbe id="mr-0001-0001-0001-000000000001" />,
    );
    expect(
      await screen.findByText(/2026 Annual Management Review/),
    ).toBeInTheDocument();
    expect(screen.getByText(/inputs:4/)).toBeInTheDocument();
    expect(screen.getByText(/outputs:2/)).toBeInTheDocument();
  });

  it("is disabled while id is null", () => {
    renderWithProviders(<MgmtReviewDetailProbe id={null} />);
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("surfaces a forbidden flag on 403", async () => {
    server.use(
      http.get("/api/v1/management-reviews/:id", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(
      <MgmtReviewDetailProbe id="mr-0001-0001-0001-000000000001" />,
    );
    expect(await screen.findByText("detail-forbidden")).toBeInTheDocument();
  });
});

// ---- useMgmtReviewApproval ----

function ApprovalProbe({ id }: { id: string | null }) {
  const { data, isSuccess, forbidden } = useMgmtReviewApproval(id);
  if (forbidden) return <div>approval-forbidden</div>;
  if (!isSuccess) return <div>loading</div>;
  return <div>{data === null ? "no-approval" : data.id}</div>;
}

describe("useMgmtReviewApproval", () => {
  it("returns null when no approval cycle exists (pre-submit)", async () => {
    renderWithProviders(<ApprovalProbe id="mr-0001-0001-0001-000000000001" />);
    expect(await screen.findByText("no-approval")).toBeInTheDocument();
  });

  it("is disabled while id is null", () => {
    renderWithProviders(<ApprovalProbe id={null} />);
    expect(screen.getByText("loading")).toBeInTheDocument();
  });
});

// ---- useMgmtReviewNextDue ----

function NextDueProbe() {
  const { data, forbidden } = useMgmtReviewNextDue();
  if (forbidden) return <div>nextdue-forbidden</div>;
  if (!data) return <div>none</div>;
  return <div>state:{data.review_state} cadence:{data.cadence_months}</div>;
}

describe("useMgmtReviewNextDue", () => {
  it("returns cadence and review_state", async () => {
    renderWithProviders(<NextDueProbe />);
    expect(await screen.findByText(/state:due_soon/)).toBeInTheDocument();
    expect(screen.getByText(/cadence:12/)).toBeInTheDocument();
  });

  it("surfaces a forbidden flag on 403", async () => {
    server.use(
      http.get("/api/v1/management-reviews/next-due", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<NextDueProbe />);
    expect(await screen.findByText("nextdue-forbidden")).toBeInTheDocument();
  });
});
