import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import type { ImportRun } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { CommitProgress } from "./CommitProgress";

function runWith(over: Partial<ImportRun>): ImportRun {
  return { ...(ingestionRunFixture as unknown as ImportRun), ...over };
}

test("renders a Committing state with the committed/failed counts from run.counts.commit", () => {
  renderWithProviders(
    <CommitProgress
      run={runWith({
        status: "Committing",
        counts: { commit: { committed: 3, failed: 1 } },
      })}
    />,
  );
  expect(screen.getByText("Committing to the vault")).toBeInTheDocument();
  expect(screen.getByLabelText("Committed so far: 3")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed so far: 1")).toBeInTheDocument();
});

test("missing commit counts degrade to zero (no crash under noUncheckedIndexedAccess)", () => {
  renderWithProviders(<CommitProgress run={runWith({ status: "Committing", counts: null })} />);
  expect(screen.getByLabelText("Committed so far: 0")).toBeInTheDocument();
  expect(screen.getByLabelText("Failed so far: 0")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderWithProviders(
    <CommitProgress run={runWith({ status: "Committing", counts: { commit: { committed: 2, failed: 0 } } })} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
