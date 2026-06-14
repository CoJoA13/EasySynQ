import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrApprovalContext } from "./DcrApprovalContext";

// The DCR_REVISE_ID from handlers.ts — the dcrDetailFixture is spread from dcrListFixture.data[0]
// which has identifier "DCR-2026-0001" and reason_text "Corrective action requires a procedure revision."
const DCR_ID = "dcr00001-0001-0001-0001-000000000001";

it("shows the DCR identity + reason for the approver", async () => {
  renderWithProviders(<DcrApprovalContext dcrId={DCR_ID} />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  expect(screen.getByText("Corrective action requires a procedure revision.")).toBeInTheDocument();
});

it("degrades calmly when the caller lacks changeRequest.read (403)", async () => {
  server.use(
    http.get(`/api/v1/dcrs/${DCR_ID}`, () =>
      HttpResponse.json({ code: "forbidden", message: "no" }, { status: 403 }),
    ),
  );
  renderWithProviders(<DcrApprovalContext dcrId={DCR_ID} />);
  expect(await screen.findByText(/not visible to you/i)).toBeInTheDocument();
});
