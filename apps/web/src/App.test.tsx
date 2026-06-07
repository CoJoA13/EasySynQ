import { screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "./test/render";
import { App } from "./App";

test("operational app renders the shell + Library at /library", async () => {
  renderWithProviders(<App />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("Document Library")).toBeInTheDocument());
  expect(screen.getAllByRole("link", { name: "Home" }).length).toBeGreaterThan(0); // shell rail
});

test("operational app with no token bounces to sign-in (in-memory tokens, post-reload)", async () => {
  sessionStorage.removeItem("es_auth_redirect");
  const login = vi.fn();
  renderWithProviders(<App />, {
    route: "/library",
    auth: { ready: true, user: null, token: null, login, logout: () => {} },
  });
  await waitFor(() => expect(login).toHaveBeenCalledTimes(1)); // auto-redirect to Keycloak
  expect(screen.getByText(/signing in/i)).toBeInTheDocument(); // interstitial, not the 401-ing shell
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
});
