import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";

test("harness renders + jest-dom + jest-axe work", async () => {
  const { container } = render(
    <MantineProvider>
      <main>
        <h1>EasySynQ</h1>
      </main>
    </MantineProvider>,
  );
  expect(screen.getByRole("heading", { name: "EasySynQ" })).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});
