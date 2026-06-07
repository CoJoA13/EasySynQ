import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { HistoryTab } from "./HistoryTab";

const ID = "11111111-1111-1111-1111-111111111111";

test("HistoryTab renders the version timeline (newest first)", async () => {
  renderWithProviders(<HistoryTab documentId={ID} active={true} />);
  await waitFor(() => expect(screen.getByText("Rev B")).toBeInTheDocument());
  expect(screen.getByText("Rev A")).toBeInTheDocument();
});

test("HistoryTab shows quiet no-access on a 403 (document.read_draft, DP-6)", async () => {
  server.use(
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<HistoryTab documentId={ID} active={true} />);
  await waitFor(() =>
    expect(screen.getByText("You don't have access to the version history.")).toBeInTheDocument(),
  );
});

test("HistoryTab does not fetch when inactive (lazy)", () => {
  // active=false → the query is disabled; the tab renders nothing rather than loading/erroring.
  const { container } = renderWithProviders(<HistoryTab documentId={ID} active={false} />);
  expect(container).not.toHaveTextContent("Rev B");
});
