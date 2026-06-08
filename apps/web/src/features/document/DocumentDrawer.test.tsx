import { expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { DocumentDrawer } from "./DocumentDrawer";

const ID = "11111111-1111-1111-1111-111111111111";

test("DocumentDrawer offers an Open full page link to the standalone route (doc 11 §4.3)", async () => {
  renderWithProviders(<DocumentDrawer documentId={ID} onClose={() => {}} />);
  const link = await screen.findByRole("link", { name: /Open full page/ });
  expect(link).toHaveAttribute("href", `/documents/${ID}`);
});
