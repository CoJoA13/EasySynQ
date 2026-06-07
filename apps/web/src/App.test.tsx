import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderWithProviders } from "./test/render";
import { App } from "./App";

test("operational app renders the shell + Library at /library", async () => {
  renderWithProviders(<App />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("Document Library")).toBeInTheDocument());
  expect(screen.getAllByRole("link", { name: "Home" }).length).toBeGreaterThan(0); // shell rail
});
