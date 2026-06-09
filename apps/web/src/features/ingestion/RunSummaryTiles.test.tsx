import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import type { ImportChecklistReviewStats, ImportRun } from "../../lib/types";
import { ingestionChecklistFixture, ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { RunSummaryTiles } from "./RunSummaryTiles";

const run = ingestionRunFixture as unknown as ImportRun;
const review = ingestionChecklistFixture.review as unknown as ImportChecklistReviewStats;

test("renders the four tiles from run.counts.by_band + the checklist review stats", async () => {
  const { container } = renderWithProviders(<RunSummaryTiles run={run} review={review} />);
  // (1) Auto-classified · High = by_band.HIGH = 2
  expect(screen.getByLabelText("Auto-classified · High: 2")).toBeInTheDocument();
  // (2) Medium = by_band.MEDIUM = 1
  expect(screen.getByLabelText("Medium: 1")).toBeInTheDocument();
  // (3) Needs decision = checklist review.undecided = 4
  expect(screen.getByLabelText("Needs decision: 4")).toBeInTheDocument();
  // (4) Kind confirmed = review.kind_confirmed / review.keep_items = 1 / 4
  expect(screen.getByLabelText("Kind confirmed: 1 of 4")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("the values render verbatim (2, 1, 4, and '1 / 4')", () => {
  renderWithProviders(<RunSummaryTiles run={run} review={review} />);
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByText("1 / 4")).toBeInTheDocument();
});

test("a missing count key / absent review degrades to 0 (never crashes, never NaN)", async () => {
  const sparse = { ...run, counts: { by_band: { HIGH: 2 } } } as unknown as ImportRun;
  // no review prop → the folded tiles fall back to 0
  const { container } = renderWithProviders(<RunSummaryTiles run={sparse} />);
  expect(screen.getByLabelText("Auto-classified · High: 2")).toBeInTheDocument();
  expect(screen.getByLabelText("Medium: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Needs decision: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind confirmed: 0 of 0")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("a null counts object degrades every tile to 0", () => {
  const empty = { ...run, counts: null } as unknown as ImportRun;
  renderWithProviders(<RunSummaryTiles run={empty} />);
  expect(screen.getByLabelText("Auto-classified · High: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind confirmed: 0 of 0")).toBeInTheDocument();
});
