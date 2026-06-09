import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { ContentBlock } from "./ContentBlock";

function wrap(block: Record<string, unknown>) {
  return render(
    <MantineProvider theme={theme}>
      <ContentBlock block={block} />
    </MantineProvider>,
  );
}

test("renders each key as a humanized label with its value", () => {
  wrap({ root_cause: "Reminders never scheduled", method: "5-whys" });
  expect(screen.getByText("Root cause")).toBeInTheDocument();
  expect(screen.getByText("Reminders never scheduled")).toBeInTheDocument();
  expect(screen.getByText("Method")).toBeInTheDocument();
});

test("renders an array value as a list", () => {
  wrap({ action_items: ["Schedule reminders", "Train planner"] });
  expect(screen.getByText("Schedule reminders")).toBeInTheDocument();
  expect(screen.getByText("Train planner")).toBeInTheDocument();
});

test("renders an HTML-looking string value as literal text (no XSS)", () => {
  const { container } = wrap({ note: "<img src=x onerror=alert(1)>" });
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
});

test("renders an empty block calmly", () => {
  wrap({});
  expect(screen.getByText(/no details/i)).toBeInTheDocument();
});
