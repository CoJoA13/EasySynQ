import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { ScorecardBandShell } from "./ScorecardBandShell";

function renderShell(headline: string, chips: string[]) {
  return render(
    <MantineProvider>
      <div data-testid="wrap">
        <ScorecardBandShell headline={headline}>
          {chips.map((c) => (
            <span key={c}>{c}</span>
          ))}
        </ScorecardBandShell>
      </div>
    </MantineProvider>,
  );
}

it("renders the headline and every chip", () => {
  renderShell("3 of 12 high or critical", ["3 critical", "9 low"]);
  const wrap = screen.getByTestId("wrap");
  expect(within(wrap).getByText("3 of 12 high or critical")).toBeInTheDocument();
  expect(within(wrap).getByText("3 critical")).toBeInTheDocument();
  expect(within(wrap).getByText("9 low")).toBeInTheDocument();
});

// The reason this shell exists. Each of the four bands set `radius` EXPLICITLY, which defeats a
// Mantine theme default, so a card-radius change costs four hand edits without a shared shell and
// one with it. The value is deliberately unchanged from the bands this replaces — the programme's
// 16px rule is not shipped (theme `defaultRadius` is `md`; S-ui-3's quadrant cards are `lg`), so
// moving it here alone would make this the only 16px surface in the app. This test's job is to pin
// that the radius now comes from ONE place and resolves through the token scale rather than a px
// literal; whoever settles §2.4 app-wide changes one line here and this assertion with it.
it("takes its radius from one place, through the token scale", () => {
  const { container } = renderShell("0 of 0", []);
  const paper = container.querySelector(".mantine-Paper-root");
  expect(paper).not.toBeNull();
  expect(paper?.getAttribute("style") ?? "").toContain("var(--mantine-radius-md)");
});

// A band with no chips must still render its headline — an empty register is a legitimate state and
// the count sentence is the thing the reader came for.
it("renders the headline with no chips at all", () => {
  renderShell("0 of 0 active", []);
  expect(screen.getByText("0 of 0 active")).toBeInTheDocument();
});
