import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { BandPreview } from "./BandPreview";

it("draws a three-zone preview and the threshold/target labels for a valid HIGHER band", () => {
  renderWithProviders(
    <BandPreview target="95" threshold="90" direction="HIGHER_IS_BETTER" />,
  );
  expect(screen.getByText(/90 at-risk/i)).toBeInTheDocument();
  expect(screen.getByText(/95 target/i)).toBeInTheDocument();
  // a labelled meter for screen readers
  expect(screen.getByRole("img", { name: /green.*amber.*red|status band/i })).toBeInTheDocument();
});

it("shows the soft warning when the threshold is on the wrong side", () => {
  renderWithProviders(
    <BandPreview target="95" threshold="96" direction="HIGHER_IS_BETTER" />,
  );
  expect(screen.getByText(/below the target/i)).toBeInTheDocument();
});

it("renders nothing structural when the target is not yet a number", () => {
  renderWithProviders(
    <BandPreview target="" threshold="" direction="HIGHER_IS_BETTER" />,
  );
  expect(document.querySelector("[role='img']")).not.toBeInTheDocument();
  expect(document.querySelector("svg")).not.toBeInTheDocument();
});
