import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { AckCoverageRing } from "./AckCoverageRing";

describe("AckCoverageRing", () => {
  test("renders the acknowledged/required ratio and percent", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 47, acknowledged: 41, pending: 6, overdue: 2 }} />);
    expect(screen.getByText("41 / 47")).toBeInTheDocument();
    expect(screen.getByText("87%")).toBeInTheDocument(); // round(41/47*100)
    expect(screen.getByText(/6 pending/)).toBeInTheDocument();
  });

  test("null coverage → an honest dash, no ring", () => {
    renderWithProviders(<AckCoverageRing coverage={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/Not yet effective/)).toBeInTheDocument();
  });

  test("flag-off zeros → not-distributed copy", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 0, acknowledged: 0, pending: 0, overdue: 0 }} />);
    expect(screen.getByText(/Not distributed for acknowledgement/)).toBeInTheDocument();
  });

  test("100% renders without dividing by anything odd", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 1, acknowledged: 1, pending: 0, overdue: 0 }} />);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });
});
