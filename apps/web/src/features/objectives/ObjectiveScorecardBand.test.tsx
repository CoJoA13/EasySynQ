import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
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
