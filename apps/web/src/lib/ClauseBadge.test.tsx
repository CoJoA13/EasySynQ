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

  const regular = screen.getByLabelText("Clause 8.4");
  const mandatory = screen.getByLabelText("Clause 7.5.3, mandatory");
  expect(regular).toHaveAttribute("data-variant", "outline");
  expect(regular).toHaveAttribute("data-clause-badge");
  expect(regular).toHaveTextContent("8.4");
  expect(mandatory).toHaveAttribute("data-variant", "outline");
  expect(mandatory).toHaveTextContent("★ 7.5.3");
  expect(mandatory.querySelector('[aria-hidden="true"]')).toHaveTextContent("★");
  expect(await axe(container)).toHaveNoViolations();
});
