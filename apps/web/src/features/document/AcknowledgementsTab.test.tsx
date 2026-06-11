import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { AcknowledgementsTab } from "./AcknowledgementsTab";

const DOC = "11111111-1111-1111-1111-111111111111";

function grantDistribute() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "ARTIFACT", selector: { id: DOC } },
        permissions: [{ key: "document.distribute", effect: "ALLOW", source: "system_override" }],
      }),
    ),
  );
}

describe("AcknowledgementsTab", () => {
  test("a plain reader sees the coverage ring + counts but NOT the named matrix", async () => {
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    expect(await screen.findByText("41 / 47")).toBeInTheDocument();
    // The matrix names are document.distribute-gated → absent for the reader.
    expect(screen.queryByText("Sam Patel")).not.toBeInTheDocument();
    expect(screen.getByText(/can view coverage but not the named/i)).toBeInTheDocument();
  });

  test("a distributor sees the named matrix with status badges + the pending avatar stack", async () => {
    grantDistribute();
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    expect(await screen.findByText("Sam Patel")).toBeInTheDocument();
    const row = screen.getByText("Sam Patel").closest("tr")!;
    expect(within(row).getByText("overdue")).toBeInTheDocument();
    // "Mara Quality" now also appears as a distribution entry (the real DistributionEditor renders it),
    // so scope the matrix assertion to the matrix table.
    const matrix = screen.getByRole("table", { name: "Acknowledgement matrix" });
    expect(within(matrix).getByText("Mara Quality")).toBeInTheDocument();
  });

  test("no Remind button anywhere (R43 omitted-not-faked)", async () => {
    grantDistribute();
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    await screen.findByText("Sam Patel");
    expect(screen.queryByRole("button", { name: /remind/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/last reminded/i)).not.toBeInTheDocument();
  });

  test("does not fetch until active", async () => {
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active={false} />);
    expect(screen.queryByText("41 / 47")).not.toBeInTheDocument();
  });
});
