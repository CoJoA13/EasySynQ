import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { RolesAdmin } from "./RolesAdmin";

test("announces role-detail loading with the role name", async () => {
  let release: (() => void) | undefined;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get("/api/v1/roles", () =>
      HttpResponse.json([
        {
          id: "ro000001-0001-0001-0001-000000000001",
          name: "Employee",
          description: "All staff",
          is_reserved: true,
        },
      ]),
    ),
    http.get("/api/v1/roles/:id", async ({ params }) => {
      await blocked;
      return HttpResponse.json({
        id: String(params.id),
        name: "Employee",
        description: "All staff",
        is_reserved: true,
        grants: [{ permission_key: "document.read", scope_template: { level: "SYSTEM" } }],
      });
    }),
  );

  renderWithProviders(<RolesAdmin token="test-token" />);
  await userEvent.click(await screen.findByRole("button", { name: /Employee/ }));
  expect(
    await screen.findByRole("status", { name: "Loading Employee role details" }),
  ).toBeInTheDocument();

  act(() => release?.());
  expect(await screen.findByText("document.read")).toBeInTheDocument();
});
