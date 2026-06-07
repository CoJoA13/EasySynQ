import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LibraryPage } from "./LibraryPage";

// DP-6: the "New document" entry shows only when the caller holds document.create (coarse SYSTEM
// scope, from GET /me/permissions). The default handler grants nothing → the entry is absent.
it("hides the New document entry without document.create", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  await screen.findByText(/Document Library/);
  await waitFor(() => expect(screen.queryByText(/loading…/i)).not.toBeInTheDocument());
  expect(screen.queryByRole("link", { name: /new document/i })).not.toBeInTheDocument();
});

it("shows the New document entry when document.create is granted", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "document.create", effect: "ALLOW", source: "role:Author" }],
      }),
    ),
  );
  renderWithProviders(<LibraryPage />, { route: "/library" });
  const link = await screen.findByRole("link", { name: /new document/i });
  expect(link).toHaveAttribute("href", "/library/new");
});
