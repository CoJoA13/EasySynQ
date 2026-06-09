import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import type { ImportRun } from "../../lib/types";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { RunSummaryTiles } from "./RunSummaryTiles";

const run = ingestionRunFixture as unknown as ImportRun;

test("renders the four metric tiles from run.counts", async () => {
  const { container } = renderWithProviders(<RunSummaryTiles run={run} />);
  // (1) Auto-classified · High = classify.band.HIGH = 2
  expect(screen.getByLabelText("Auto-classified · High: 2")).toBeInTheDocument();
  // (2) Medium = classify.band.MEDIUM = 1
  expect(screen.getByLabelText("Medium: 1")).toBeInTheDocument();
  // (3) Needs decision = queues.needs = 4
  expect(screen.getByLabelText("Needs decision: 4")).toBeInTheDocument();
  // (4) Kind confirmed = review.kind_confirmed / review.keep_items = 1 / 4
  expect(screen.getByLabelText("Kind confirmed: 1 of 4")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("the values render verbatim (2, 1, 4, and '1 / 4')", () => {
  renderWithProviders(<RunSummaryTiles run={run} />);
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByText("1 / 4")).toBeInTheDocument();
});

test("a tile whose count key is missing degrades to 0 (never crashes, never NaN)", async () => {
  const sparse = { ...run, counts: { classify: { band: { HIGH: 2 } } } } as unknown as ImportRun;
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
