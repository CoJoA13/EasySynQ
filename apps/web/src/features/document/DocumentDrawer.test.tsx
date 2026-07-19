import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DocumentDrawer } from "./DocumentDrawer";

const ID = "11111111-1111-1111-1111-111111111111";

test("DocumentDrawer offers an Open full page link to the standalone route (doc 11 §4.3)", async () => {
  renderWithProviders(<DocumentDrawer documentId={ID} onClose={() => {}} />);
  const link = await screen.findByRole("link", { name: /Open full page/ });
  expect(link).toHaveAttribute("href", `/documents/${ID}`);
});

test("shows a retryable error (not a blank drawer) when the document load fails", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () =>
      HttpResponse.json({ code: "boom", title: "nope" }, { status: 500 }),
    ),
  );
  renderWithProviders(
    <DocumentDrawer documentId="00000000-0000-0000-0000-0000000000ff" onClose={() => {}} />,
  );
  expect(await screen.findByText("Couldn't load this document")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
