import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../test/render";
import { ClauseBadge } from "./ClauseBadge";

it("uses one outlined, accessible treatment and preserves mandatory meaning", async () => {
  const { container } = renderWithProviders(
    <>
      <ClauseBadge clause="8.4" />
      <ClauseBadge clause="7.5.3" starred />
    </>,
  );

  const regularText = screen.getByText("Clause 8.4");
  const mandatoryText = screen.getByText("Clause 7.5.3, mandatory");
  const regular = regularText.closest("[data-clause-badge]");
  const mandatory = mandatoryText.closest("[data-clause-badge]");
  expect(regular).toHaveAttribute("data-variant", "outline");
  expect(regular).toHaveAttribute("data-clause-badge");
  expect(regularText).not.toHaveAttribute("aria-hidden");
  expect(mandatory).toHaveAttribute("data-variant", "outline");
  expect(mandatoryText).not.toHaveAttribute("aria-hidden");
  expect(mandatory?.querySelector('[aria-hidden="true"]')).toHaveTextContent("★ 7.5.3");
  expect(await axe(container)).toHaveNoViolations();
});
