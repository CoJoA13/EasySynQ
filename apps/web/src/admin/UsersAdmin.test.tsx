import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { UsersAdmin } from "./UsersAdmin";

const USER_ID = "us000001-0001-0001-0001-000000000001";

test("a manage action failure renders inside the open user drawer", async () => {
  server.use(
    http.get("/api/v1/users", () =>
      HttpResponse.json([
        {
          id: USER_ID,
          keycloak_subject: "kc-mara",
          display_name: "Mara Quality",
          email: "mara@example.com",
          status: "ACTIVE",
          mfa_enrolled: true,
          is_guest: false,
          roles: [],
        },
      ]),
    ),
    http.get("/api/v1/users/:id/roles", () => HttpResponse.json([])),
    http.get("/api/v1/users/:id/overrides", () => HttpResponse.json([])),
    http.post("/api/v1/users/:id/roles", () =>
      HttpResponse.json(
        {
          code: "role_conflict",
          title: "Role conflict",
          detail: "Role already assigned.",
        },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<UsersAdmin token="test-token" />);

  await user.click(await screen.findByRole("button", { name: "Manage" }));
  const drawer = await screen.findByRole("dialog");
  await user.click(await within(drawer).findByLabelText("Assign a role"));
  await user.click(await screen.findByRole("option", { name: "Employee" }));
  await user.click(within(drawer).getByRole("button", { name: "Assign" }));

  expect(await within(drawer).findByText("Action failed")).toBeInTheDocument();
  expect(within(drawer).getByText("role_conflict: Role already assigned.")).toBeInTheDocument();
});
