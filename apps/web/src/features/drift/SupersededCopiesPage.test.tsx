import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { supersededCopiesFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { SupersededCopiesPage } from "./SupersededCopiesPage";

describe("SupersededCopiesPage", () => {
  test("renders the totals headline and one row per version", async () => {
    renderWithProviders(<SupersededCopiesPage />);
    expect(await screen.findByText(/2 versions · 5 copies/)).toBeInTheDocument();
    expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("SOP-OBS-001")).toBeInTheDocument();
    // the obsoleted document has no current revision → an em-dash cell
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  test("identifier links to the document page", async () => {
    renderWithProviders(<SupersededCopiesPage />);
    const link = await screen.findByRole("link", { name: "SOP-PUR-014" });
    expect(link).toHaveAttribute("href", "/documents/11111111-1111-1111-1111-111111111111");
  });

  test("pagination drives the server offset", async () => {
    const offsets: string[] = [];
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
        const sp = new URL(request.url).searchParams;
        offsets.push(sp.get("offset") ?? "?");
        return HttpResponse.json({
          total: { versions: 120, copies: 300 },
          items: supersededCopiesFixture.items,
        });
      }),
    );
    renderWithProviders(<SupersededCopiesPage />);
    await screen.findByText("SOP-PUR-014");
    await userEvent.click(screen.getByRole("button", { name: "2" })); // page 2 of 3 (120/50)
    await waitFor(() => expect(offsets).toContain("50"));
  });

  test("empty set renders the calm empty state", async () => {
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", () =>
        HttpResponse.json({ total: { versions: 0, copies: 0 }, items: [] }),
      ),
    );
    renderWithProviders(<SupersededCopiesPage />);
    expect(
      await screen.findByText("No outstanding copies of superseded versions."),
    ).toBeInTheDocument();
  });

  test("403 renders the calm no-access panel", async () => {
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<SupersededCopiesPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });
});
