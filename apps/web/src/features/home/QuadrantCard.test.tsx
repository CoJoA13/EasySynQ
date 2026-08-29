import { screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { QuadrantCard, TileNoAccess } from "./QuadrantCard";
import { quadrantSignal } from "./rag";
import { StatLine } from "./StatLine";

it("renders the phase, its clause range and the derived signal in the header band", () => {
  renderWithProviders(
    <QuadrantCard
      phase="PLAN"
      clauseLabel="Cl 4–6"
      signal={quadrantSignal([{ value: 4, label: "document reviews overdue", rag: "amber" }])}
      openTo="/objectives"
      openLabel="Open objectives"
    >
      <StatLine value="6 / 8" label="objectives on target" tone="green" />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /plan quadrant/i });
  expect(within(card).getByText("PLAN")).toBeInTheDocument();
  expect(within(card).getByText("Cl 4–6")).toBeInTheDocument();
  // The header states the observation, not a verdict.
  expect(within(card).getByText("4 document reviews overdue")).toBeInTheDocument();
  const open = within(card).getByRole("link", { name: /open objectives/i });
  expect(open).toHaveAttribute("href", "/objectives");
});

it("never renders a compliance verdict in the header", () => {
  // §2.3: an ACT header once read "✓ on track" above six open CAPAs. The header must name the
  // count that drove the severity instead — this is the rendered-side guard for that rule.
  renderWithProviders(
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      signal={quadrantSignal([
        { value: 6, label: "CAPAs open", rag: "amber" },
        { value: 0, label: "NCRs awaiting disposition", rag: "green" },
      ])}
      openTo="/capa"
      openLabel="Open CAPA"
    >
      <StatLine value={6} label="CAPAs open" tone="amber" />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /act quadrant/i });
  expect(within(card).getByText("6 CAPAs open")).toBeInTheDocument();
  for (const verdict of ["On track", "Needs attention", "Action required"]) {
    expect(within(card).queryByText(verdict)).not.toBeInTheDocument();
  }
});

it("omits the signal entirely when there is none (loading / no-access)", () => {
  renderWithProviders(
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      signal={null}
      openTo="/capa"
      openLabel="Open CAPA"
    >
      <TileNoAccess />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /act quadrant/i });
  expect(within(card).getByText("ACT")).toBeInTheDocument();
  expect(screen.getByText(/no access to this section/i)).toBeInTheDocument();
  // Nothing is asserted about the quadrant's health when its data could not be read: the band still
  // names the phase, but carries no observation and no severity.
  const band = within(card).getByRole("group", { name: "ACT signal" });
  expect(within(band).queryByText(/status:/i)).not.toBeInTheDocument();
  expect(band).toHaveTextContent(/^ACT\s*Cl 10$/);
});

it("keeps the phase hue decorative — the signal survives without colour", () => {
  renderWithProviders(
    <QuadrantCard
      phase="CHECK"
      clauseLabel="Cl 9"
      signal={quadrantSignal([{ value: 2, label: "open audits", rag: "red" }])}
      openTo="/audits"
      openLabel="Open audits"
    >
      <StatLine value={2} label="open audits" tone="red" />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /check quadrant/i });
  // The glyph is the non-colour channel (DP-5) and is hidden from AT; the TEXT carries the meaning,
  // so a reader with colour removed still gets the count and its label.
  expect(within(card).getByText("2 open audits")).toBeInTheDocument();
  // Scoped to the BAND. A card-wide [aria-hidden="true"] query also matches the StatLine's own tone
  // glyph and the "→" in the Open link, so it was satisfied even with the header glyph deleted.
  const band = within(card).getByRole("group", { name: "CHECK signal" });
  const glyph = band.querySelector('[aria-hidden="true"]');
  expect(glyph, "the header band renders no non-colour glyph").not.toBeNull();
  expect(glyph).toHaveTextContent(/\S/);
});

it("announces an informational signal as informational, not as absent data", () => {
  // RAG_META.neutral.label is "No data", which is right for an ABSENT reading and wrong for a
  // present-but-informational one. StatLine already remaps neutral to "Informational"; the header
  // has to agree, or a screen reader hears "Status: No data" beside a real observed count.
  renderWithProviders(
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      signal={quadrantSignal([{ value: 3, label: "initiatives in progress", rag: "neutral" }])}
      openTo="/capa"
      openLabel="Open CAPA"
    >
      <StatLine value={3} label="initiatives in progress" tone="neutral" />
    </QuadrantCard>,
  );
  const band = within(screen.getByRole("group", { name: /act quadrant/i })).getByRole("group", {
    name: "ACT signal",
  });
  expect(within(band).getByText("3 initiatives in progress")).toBeInTheDocument();
  expect(within(band).getByText(/status: informational/i)).toBeInTheDocument();
  expect(within(band).queryByText(/no data/i)).not.toBeInTheDocument();
});
