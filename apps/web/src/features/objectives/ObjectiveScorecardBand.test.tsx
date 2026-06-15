import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { TONE_GLYPH } from "../../lib/status";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";

const BY_RAG = { green: 1, amber: 1, red: 1, unmeasured: 1 };

it("renders the on-target headline and each RAG count, accessibly", async () => {
  const { container } = renderWithProviders(
    <ObjectiveScorecardBand total={4} onTarget={1} byRag={BY_RAG} />,
  );
  expect(screen.getByText(/1\s*\/\s*4 on target/i)).toBeInTheDocument();
  expect(screen.getByText("1 green")).toBeInTheDocument();
  expect(screen.getByText("1 amber")).toBeInTheDocument();
  expect(screen.getByText("1 red")).toBeInTheDocument();
  expect(screen.getByText("1 unmeasured")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("carries each RAG count on a canonical StatusBadge (tone glyph + accessible name)", () => {
  renderWithProviders(<ObjectiveScorecardBand total={4} onTarget={1} byRag={BY_RAG} />);
  // Each chip gets the canonical non-colour glyph for its RAG → tone mapping plus an accessible name
  // (status is never colour-only, DP-7): green→success ✓, amber→warning ◔, red→danger ✕, unmeasured→neutral ○.
  expect(screen.getByLabelText("Objectives: 1 green")).toBeInTheDocument();
  expect(screen.getByLabelText("Objectives: 1 amber")).toBeInTheDocument();
  expect(screen.getByLabelText("Objectives: 1 red")).toBeInTheDocument();
  expect(screen.getByLabelText("Objectives: 1 unmeasured")).toBeInTheDocument();
  // All four canonical glyphs are present (one per chip).
  expect(screen.getByText(TONE_GLYPH.success)).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
});
