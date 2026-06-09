import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { MantineProvider } from "@mantine/core";
import { ImportStatusBadge } from "./ImportStatusBadge";

function renderBadge(status: string) {
  return render(
    <MantineProvider>
      <ImportStatusBadge status={status} />
    </MantineProvider>,
  );
}

test("renders the label + an aria-label for a known status", () => {
  const { getByText, getByLabelText } = renderBadge("Proposed");
  expect(getByText("Proposed")).toBeInTheDocument();
  expect(getByLabelText("Run status: Proposed")).toBeInTheDocument();
});

test("maps the additive commit stages (Committing, Completed)", () => {
  expect(renderBadge("Committing").getByLabelText("Run status: Committing")).toBeInTheDocument();
  expect(renderBadge("Completed").getByLabelText("Run status: Completed")).toBeInTheDocument();
});

test("degrades calmly for an unknown/additive status (no crash, raw label)", () => {
  const { getByText, getByLabelText } = renderBadge("SomeFutureStage");
  expect(getByText("SomeFutureStage")).toBeInTheDocument();
  expect(getByLabelText("Run status: SomeFutureStage")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderBadge("Reviewing");
  expect(await axe(container)).toHaveNoViolations();
});
