import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { MantineProvider } from "@mantine/core";
import { TONE_GLYPH } from "../../lib/status";
import { ImportStatusBadge } from "./ImportStatusBadge";

function renderBadge(status: string) {
  return render(
    <MantineProvider>
      <ImportStatusBadge status={status} />
    </MantineProvider>,
  );
}

test("renders the label + a non-colour glyph + an aria-label for a known status", () => {
  const { getByText, getByLabelText } = renderBadge("Proposed");
  expect(getByText("Proposed")).toBeInTheDocument();
  expect(getByLabelText("Run status: Proposed")).toBeInTheDocument();
  // Proposed → warning: the glyph is the non-colour channel (status is never colour-only, DP-7).
  expect(getByText(TONE_GLYPH.warning)).toBeInTheDocument();
});

test("maps the additive commit stages (Committing, Completed) onto their tones", () => {
  const committing = renderBadge("Committing");
  expect(committing.getByLabelText("Run status: Committing")).toBeInTheDocument();
  expect(committing.getByText(TONE_GLYPH.info)).toBeInTheDocument(); // in-progress → info
  const completed = renderBadge("Completed");
  expect(completed.getByLabelText("Run status: Completed")).toBeInTheDocument();
  expect(completed.getByText(TONE_GLYPH.success)).toBeInTheDocument(); // done → success
});

test("maps a failure to danger and a cancellation to neutral", () => {
  expect(renderBadge("Failed").getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  expect(renderBadge("Cancelled").getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
});

test("degrades calmly for an unknown/additive status (no crash, raw label, neutral glyph)", () => {
  const { getByText, getByLabelText } = renderBadge("SomeFutureStage");
  expect(getByText("SomeFutureStage")).toBeInTheDocument();
  expect(getByLabelText("Run status: SomeFutureStage")).toBeInTheDocument();
  expect(getByText(TONE_GLYPH.neutral)).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderBadge("Reviewing");
  expect(await axe(container)).toHaveNoViolations();
});
