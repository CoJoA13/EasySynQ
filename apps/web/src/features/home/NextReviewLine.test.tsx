import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { MgmtReviewNextDue } from "../../lib/types";
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { RAG_META } from "./rag";
import { NextReviewLine } from "./NextReviewLine";

const NEXT_DUE = "/api/v1/management-reviews/next-due";
const respond = (body: MgmtReviewNextDue) => http.get(NEXT_DUE, () => HttpResponse.json(body));

it("shows the next-due date with an amber glyph for due_soon (default fixture)", async () => {
  // The global handler serves due_soon / 2026-06-01.
  renderWithProviders(<NextReviewLine />);
  const line = await screen.findByText("Next management review due 2026-06-01");
  // The status glyph carries the tone (DP-7 redundant channel) — amber here.
  await waitFor(() =>
    expect(
      screen.getByLabelText("Next management review due 2026-06-01").textContent,
    ).toContain(RAG_META.amber.glyph),
  );
  expect(line).toBeInTheDocument();
});

it("renders an overdue line with the red glyph", async () => {
  server.use(
    respond({
      cadence_months: 12,
      last_review_effective_from: "2024-06-01",
      next_review_due: "2025-06-01",
      review_state: "overdue",
      owner_configured: true,
    }),
  );
  renderWithProviders(<NextReviewLine />);
  const line = await screen.findByText("Management review overdue (was due 2025-06-01)");
  await waitFor(() =>
    expect(line.closest("[aria-label]")?.textContent).toContain(RAG_META.red.glyph),
  );
});

it("shows a neutral cadence-not-configured line when owner_configured is false", async () => {
  server.use(
    respond({
      cadence_months: 12,
      last_review_effective_from: null,
      next_review_due: null,
      review_state: null,
      owner_configured: false,
    }),
  );
  renderWithProviders(<NextReviewLine />);
  const line = await screen.findByText("Review cadence not configured");
  expect(line.closest("[aria-label]")?.textContent).toContain(RAG_META.neutral.glyph);
});

it("shows a neutral no-review-released line when no review has been released", async () => {
  server.use(
    respond({
      cadence_months: 12,
      last_review_effective_from: null,
      next_review_due: null,
      review_state: null,
      owner_configured: true,
    }),
  );
  renderWithProviders(<NextReviewLine />);
  const line = await screen.findByText("No management review released yet");
  expect(line.closest("[aria-label]")?.textContent).toContain(RAG_META.neutral.glyph);
});

function ForbiddenProbe() {
  const { forbidden } = useMgmtReviewNextDue();
  return <div>{forbidden ? "forbidden-settled" : "pending"}</div>;
}

it("renders nothing on a 403 (never drags the tile red)", async () => {
  server.use(
    http.get(NEXT_DUE, () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
  );
  const { container } = renderWithProviders(
    <>
      <NextReviewLine />
      <ForbiddenProbe />
    </>,
  );
  // Wait for the forbidden read to settle (the sibling probe flips), then assert the line emitted
  // NOTHING. `toBeEmptyDOMElement` is unreliable here — MantineProvider injects a <style> node into
  // the container — so assert the StatLine's aria-label group never rendered instead.
  await screen.findByText("forbidden-settled");
  expect(container.querySelector('[aria-label^="Next management review"]')).toBeNull();
  expect(container.querySelector('[aria-label="Review cadence not configured"]')).toBeNull();
  expect(container.querySelector('[aria-label^="No management review"]')).toBeNull();
});
