import { axe } from "jest-axe";
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { HomePage } from "./HomePage";

test("HomePage renders a calm welcome heading, accessibly", async () => {
  const { container } = renderWithProviders(<HomePage />);
  expect(screen.getByRole("heading", { name: /qms health/i })).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});
