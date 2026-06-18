import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { ProcessesAdmin } from "./ProcessesAdmin";

// Grant process.assign_owner at the coarse SYSTEM scope the tab probes (the write affordances gate).
function allowAssignOwner() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "process.assign_owner", effect: "ALLOW" }],
      }),
    ),
  );
}

test("lists processes and, without process.assign_owner, the drawer is read-only", async () => {
  const u = userEvent.setup();
  renderWithProviders(<ProcessesAdmin token="test-token" />);

  // The roster renders both seeded processes.
  expect(await screen.findByText("Purchasing")).toBeInTheDocument();
  expect(screen.getByText("Production")).toBeInTheDocument();

  // Open the first process's owner drawer.
  await u.click(screen.getAllByRole("button", { name: /manage owners/i })[0]!);
  const dialog = await screen.findByRole("dialog");
  // The current owner (from the default fixture) is shown by display name.
  expect(await within(dialog).findByText("Diego Owner")).toBeInTheDocument();
  // The default permissions (empty) hide ALL write affordances — no Owner Select, no Assign button,
  // no per-owner Remove control.
  expect(within(dialog).queryByLabelText("Owner")).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: /assign owner/i })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: /remove owner/i })).not.toBeInTheDocument();
  expect(within(dialog).getByText(/need process\.assign_owner/i)).toBeInTheDocument();
});

test("assigns an owner — sends user_id to POST /processes/{id}/owner", async () => {
  allowAssignOwner();
  let body: Record<string, unknown> | null = null;
  let path = "";
  server.use(
    // Start with no owners so the directory names appear only in the Owner Select (no dupes).
    http.get("/api/v1/processes/:id/owners", () => HttpResponse.json([])),
    http.post("/api/v1/processes/:id/owner", async ({ request, params }) => {
      path = String(params.id);
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(
        {
          process_id: path,
          user_id: (body as { user_id: string }).user_id,
          org_role_id: "or000099-0099-0099-0099-000000000099",
          org_role_assignment_id: "oa000001-0001-0001-0001-000000000001",
          role_assignment_id: "ra000001-0001-0001-0001-000000000001",
          bound_scope: {
            level: "PROCESS",
            selector: { process_ids: [path] },
            managed_by: "owner_assignment",
          },
        },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProcessesAdmin token="test-token" />);

  await u.click((await screen.findAllByRole("button", { name: /manage owners/i }))[0]!);
  const dialog = await screen.findByRole("dialog");
  await u.click(await within(dialog).findByLabelText("Owner"));
  await u.click(await screen.findByRole("option", { name: "Mara Quality" }));
  await u.click(within(dialog).getByRole("button", { name: /assign owner/i }));

  await waitFor(() => expect(body).not.toBeNull());
  expect(path).toBe("pr000001-0001-0001-0001-000000000001");
  expect(body!["user_id"]).toBe("bbbb1111-1111-1111-1111-111111111111");
});

test("removes an owner — DELETEs /processes/{id}/owner/{user_id}", async () => {
  allowAssignOwner();
  let deletedPath = "";
  server.use(
    http.delete("/api/v1/processes/:id/owner/:userId", ({ params }) => {
      deletedPath = `${String(params.id)}/${String(params.userId)}`;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProcessesAdmin token="test-token" />);

  await u.click((await screen.findAllByRole("button", { name: /manage owners/i }))[0]!);
  const dialog = await screen.findByRole("dialog");
  // The default owners fixture has Diego — remove him.
  await u.click(await within(dialog).findByRole("button", { name: /remove owner/i }));
  await waitFor(() =>
    expect(deletedPath).toBe(
      "pr000001-0001-0001-0001-000000000001/bbbb2222-2222-2222-2222-222222222222",
    ),
  );
});

test("a 403 on the process list degrades to a calm No access panel", async () => {
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProcessesAdmin token="test-token" />);
  expect(await screen.findByText(/no access/i)).toBeInTheDocument();
  expect(screen.getByText(/need process\.read/i)).toBeInTheDocument();
});
