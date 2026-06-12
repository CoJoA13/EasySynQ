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
  });

  test("renders em-dashes for absent optionals", () => {
    renderWithProviders(
      <ObjectiveCommitmentContext
        commitment={{ ...commitment, at_risk_threshold: null, baseline_value: null }}
      />,
    );
    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});
