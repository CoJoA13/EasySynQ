import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { ObjectiveCommitmentContext } from "./ObjectiveCommitmentContext";

// Pinned to the api build_commitment shape — decimals are STRINGS ("95", never 95).
const commitment = {
  target_value: "95",
  unit: "%",
  direction: "HIGHER_IS_BETTER" as const,
  due_date: "2026-12-31",
  at_risk_threshold: "90",
  baseline_value: "80",
  policy_id: null,
};

describe("ObjectiveCommitmentContext", () => {
  test("renders the frozen commitment under review", () => {
    renderWithProviders(
      <ObjectiveCommitmentContext
        commitment={commitment}
        title="On-time delivery"
        identifier="OBJ-001"
      />,
    );
    expect(screen.getByText("OBJ-001")).toBeInTheDocument();
    expect(screen.getByText("On-time delivery")).toBeInTheDocument();
    expect(screen.getByText("The objective commitment you are approving.")).toBeInTheDocument();
    expect(screen.getByText("95 %")).toBeInTheDocument();
    expect(screen.getByText("Higher is better")).toBeInTheDocument();
    expect(screen.getByText("2026-12-31")).toBeInTheDocument();
    expect(screen.getByText("90 %")).toBeInTheDocument();
    expect(screen.getByText("80 %")).toBeInTheDocument();
    // policy_id null → the Quality Policy row reads an em-dash, never silently hidden
    expect(screen.getByText("Quality Policy")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  test("renders the policy link when the frozen commitment carries policy_id", () => {
    renderWithProviders(
      <ObjectiveCommitmentContext
        commitment={{ ...commitment, policy_id: "po000001-0001-0001-0001-000000000001" }}
      />,
    );
    expect(screen.getByText("Linked to the Quality Policy")).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  test("renders em-dashes for absent optionals", () => {
    renderWithProviders(
      <ObjectiveCommitmentContext
        commitment={{ ...commitment, at_risk_threshold: null, baseline_value: null }}
      />,
    );
    expect(screen.getAllByText("—")).toHaveLength(3); // threshold + baseline + policy
  });

  test("renders was→now for changed fields and plain values for unchanged ones on a revision", () => {
    const previous = { ...commitment, target_value: "95" };
    const current = { ...commitment, target_value: "97" };
    renderWithProviders(
      <ObjectiveCommitmentContext commitment={current} previous={previous} />,
    );
    // Target changed → shows arrow
    expect(screen.getByText("95 % → 97 %")).toBeInTheDocument();
    // Due-date unchanged → plain value, no arrow in that cell (only the target cell and the subtitle have arrows)
    expect(screen.getByText("2026-12-31")).toBeInTheDocument();
    // Revision subtitle contains the arrow text
    expect(screen.getByText(/changes shown as was → now/i)).toBeInTheDocument();
    // The due-date cell value contains no arrow (assert the plain date renders without an arrow)
    expect(screen.queryByText(/2026-12-31.*→/)).toBeNull();
  });

  test("renders plain values when there is no previous commitment (first release)", () => {
    renderWithProviders(
      <ObjectiveCommitmentContext commitment={{ ...commitment, target_value: "97" }} />,
    );
    // No arrow anywhere
    expect(screen.queryByText(/→/)).toBeNull();
    // Target renders plain
    expect(screen.getByText("97 %")).toBeInTheDocument();
    // Original subtitle
    expect(
      screen.getByText("The objective commitment you are approving."),
    ).toBeInTheDocument();
  });
});
