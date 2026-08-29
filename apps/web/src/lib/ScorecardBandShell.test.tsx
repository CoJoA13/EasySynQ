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

// The reason this shell exists. The four bands passed `radius="md"` explicitly, so the Mantine
// theme's Paper default could never reach them and the programme's 16px card rule would have been
// four hand edits. Asserting the resolved custom property (not a px literal) keeps the palette in
// tokens.css as the single source of truth — a hardcoded "16px" here would be a second one.
it("carries the 16px card radius from the design token, not a literal", () => {
  const { container } = renderShell("0 of 0", []);
  const paper = container.querySelector(".mantine-Paper-root");
  expect(paper).not.toBeNull();
  expect(paper?.getAttribute("style") ?? "").toContain("var(--mantine-radius-xl)");
});

// A band with no chips must still render its headline — an empty register is a legitimate state and
// the count sentence is the thing the reader came for.
it("renders the headline with no chips at all", () => {
  renderShell("0 of 0 active", []);
  expect(screen.getByText("0 of 0 active")).toBeInTheDocument();
});
