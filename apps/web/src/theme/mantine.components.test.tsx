import { Button, Drawer, MantineProvider, Modal } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { theme } from "./mantine";

it("gives every Modal close button an accessible name", async () => {
  renderWithProviders(
    <Modal opened onClose={vi.fn()} title="Example dialog">
      Dialog content
    </Modal>,
  );

  expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  // Modal content is portalled outside Testing Library's render container.
  expect(await axe(document.body)).toHaveNoViolations();
});

it("gives every Drawer close button an accessible name", async () => {
  renderWithProviders(
    <Drawer opened onClose={vi.fn()} title="Example drawer">
      Drawer content
    </Drawer>,
  );

  expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  // Drawer content is portalled outside Testing Library's render container.
  expect(await axe(document.body)).toHaveNoViolations();
});

it("emits every scale into Mantine's CSS variables as an --es-* token reference", () => {
  // Stronger than asserting the theme OBJECT: this proves the values survive Mantine's variable
  // resolver and reach the emitted stylesheet. Before S-ui-1 these all carried Mantine defaults.
  render(<MantineProvider theme={theme}>probe</MantineProvider>);
  const css = [...document.querySelectorAll("style")].map((s) => s.textContent ?? "").join("\n");
  const emitted = (name: string) =>
    new RegExp(`${name.replaceAll("-", "\\-")}:\\s*([^;}]*)`).exec(css)?.[1]?.trim();

  expect(emitted("--mantine-font-size-sm")).toBe("var(--es-fs-body)");
  expect(emitted("--mantine-spacing-md")).toBe("var(--es-space-5)");
  expect(emitted("--mantine-radius-md")).toBe("var(--es-radius-md)");
  expect(emitted("--mantine-shadow-md")).toBe("var(--es-shadow-md)");
  expect(emitted("--mantine-h1-font-size")).toBe("var(--es-fs-h1)");

  // --mantine-line-height is a GLOBAL default inherited by elements that set their own font-size,
  // so it must be the UNITLESS ratio. A px value here crowds <Code> and anything like it.
  expect(emitted("--mantine-line-height")).toBe("var(--es-lhr-h3)");
});

it("leaves --mantine-color-dimmed to tokens.css alone", () => {
  // The dimmed remap lives in tokens.css and wins on source order. If a theme change ever made
  // Mantine emit this variable itself, its runtime-injected block would land AFTER the bundled
  // stylesheet and silently restore the failing grey across ~331 c="dimmed" call sites — with
  // every existing test still green. This is the tripwire for that.
  render(<MantineProvider theme={theme}>probe</MantineProvider>);
  const css = [...document.querySelectorAll("style")].map((s) => s.textContent ?? "").join("\n");
  expect(css).not.toContain("--mantine-color-dimmed");
});

it("gives a filled Button a label chosen for its own fill, not a blanket white", () => {
  // The direct guard for the autoContrast fix, exercised through Mantine's real variant resolver
  // rather than recomputed from the theme object. Without autoContrast every one of these is
  // var(--mantine-color-white): on red that is 3.84:1 and on yellow 2.48:1. The app has ten filled
  // buttons on palette colours, ConfirmDestructive's red confirm among them.
  const { container } = render(
    <MantineProvider theme={theme}>
      <Button color="red">destructive</Button>
      <Button color="yellow">caution</Button>
      <Button>primary</Button>
    </MantineProvider>,
  );
  const labelVar = (index: number) =>
    /--button-color:\s*([^;]+)/
      .exec(container.querySelectorAll("button")[index]?.getAttribute("style") ?? "")?.[1]
      ?.trim();

  // red-7 (#f03e3e) and yellow-7 (#f59f00) are both above the luminance threshold -> black.
  expect(labelVar(0)).toBe("var(--mantine-color-black)");
  expect(labelVar(1)).toBe("var(--mantine-color-black)");
  // brand-7 (#0a7a6f) is below it -> white, at 5.22:1.
  expect(labelVar(2)).toBe("var(--mantine-color-white)");
});
