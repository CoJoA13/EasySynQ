import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { riskListFixture } from "../../test/msw/handlers";
import { RiskMatrix } from "./RiskMatrix";

it("renders an a11y summary and the band legend", async () => {
  const { container } = renderWithProviders(<RiskMatrix rows={riskListFixture.data} />);
  // the fixture: 4 rows, 2 of them critical+high (danger-tone)
  expect(
    screen.getByRole("img", { name: /risk matrix.*4 risks plotted; 2 high or critical/i }),
  ).toBeInTheDocument();
  // the legend carries the band tone + meaning + the achievable-rating range (the threshold key)
  expect(screen.getByLabelText("Band: Critical 20–25")).toBeInTheDocument();
  expect(screen.getByLabelText("Band: Low 1–5")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("summarises an empty register as 0 plotted", () => {
  renderWithProviders(<RiskMatrix rows={[]} />);
  expect(
    screen.getByRole("img", { name: /risk matrix.*0 risks plotted; 0 high or critical/i }),
  ).toBeInTheDocument();
});
