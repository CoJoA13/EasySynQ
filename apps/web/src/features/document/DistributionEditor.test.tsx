import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { distributionFixture } from "../../test/msw/handlers";
import { DistributionEditor } from "./DistributionEditor";

const DOC = "11111111-1111-1111-1111-111111111111";

describe("DistributionEditor", () => {
  test("toggling the ack-required flag POSTs acknowledgement_required", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/v1/documents/:id/distribution", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(distributionFixture);
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    await userEvent.click(screen.getByLabelText(/require acknowledgement/i));
    await waitFor(() => expect(body).toEqual({ acknowledgement_required: false }));
  });

  test("lists existing entries and deletes one", async () => {
    let deleted: string | null = null;
    server.use(
      http.delete("/api/v1/documents/:id/distribution/:entryId", ({ params }) => {
        deleted = String(params.entryId);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    // the entry's target_id resolves to a display name once the user-directory query loads.
    const row = (await screen.findByText("Mara Quality")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: /remove/i }));
    await waitFor(() => expect(deleted).toBe("de000001-0001-0001-0001-000000000001"));
  });

  test("adds a user entry → POSTs add_entries with the user target", async () => {
    let body: { add_entries?: { target_type: string; target_id: string }[] } | null = null;
    server.use(
      http.post("/api/v1/documents/:id/distribution", async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(distributionFixture);
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    // pick a user from the directory select (Diego), then Add.
    await userEvent.click(screen.getByLabelText(/add recipient/i));
    await userEvent.click(await screen.findByText("Diego Owner"));
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    await waitFor(() =>
      expect(body?.add_entries?.[0]).toMatchObject({
        target_type: "user",
        target_id: "bbbb2222-2222-2222-2222-222222222222",
      }),
    );
  });

  test("a 422 target_kind_deferred never happens — process/folder are not offered", async () => {
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    await userEvent.click(screen.getByLabelText(/add recipient/i));
    // the target-type control offers only user + role.
    expect(screen.getByRole("radio", { name: /user/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /role/i })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /process/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /folder/i })).not.toBeInTheDocument();
  });
});
