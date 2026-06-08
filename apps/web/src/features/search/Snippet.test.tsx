import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { Snippet } from "./Snippet";

test("wraps <b>…</b> segments in a <mark> and leaves the rest as text", () => {
  const { container } = renderWithProviders(<Snippet text="…<b>Supplier</b> Selection" />);
  const mark = container.querySelector("mark");
  expect(mark?.textContent).toBe("Supplier");
  expect(container.textContent).toContain("Selection");
});

test("renders embedded markup as literal text — no HTML injection", () => {
  const { container } = renderWithProviders(
    <Snippet text="Title <script>alert(1)</script> <b>hit</b>" />,
  );
  // The <script> is text, not a real element — so no <script> node was created.
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
  expect(container.querySelector("mark")?.textContent).toBe("hit");
});

test("renders nothing for an empty snippet", () => {
  const { container } = renderWithProviders(<Snippet text="" />);
  // MantineProvider injects a <style> into the container in jsdom — check no content node
  expect(container.querySelector("span")).toBeNull();
});
