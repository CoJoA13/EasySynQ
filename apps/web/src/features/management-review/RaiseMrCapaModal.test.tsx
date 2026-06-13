import { expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RaiseMrCapaModal } from "./RaiseMrCapaModal";

const RID = "mr-0001-0001-0001-000000000001";
const OID = "ro-2";

it("requires a severity, posts it, and calls onCreated with the spawned capa id", async () => {
  server.use(
    http.post(
      `/api/v1/management-reviews/${RID}/outputs/${OID}/raise-capa`,
      async ({ request }) => {
        const body = (await request.json()) as { severity: string };
        expect(body.severity).toBe("Major");
        return HttpResponse.json(
          {
            id: OID,
            management_review_id: RID,
            output_type: "ACTION",
            description: "x",
            owner_user_id: null,
            due_date: null,
            spawned_task_id: null,
            spawned_capa_id: "capa-99",
          },
          { status: 201 },
        );
      },
    ),
  );
  const onCreated = vi.fn();
  renderWithProviders(
    <RaiseMrCapaModal opened reviewId={RID} outputId={OID} onClose={() => {}} onCreated={onCreated} />,
  );
  const raise = screen.getByRole("button", { name: "Raise CAPA" });
  expect(raise).toBeDisabled();
  await userEvent.click(await screen.findByLabelText(/^Severity/));
  await userEvent.click(await screen.findByText("Major"));
  expect(raise).toBeEnabled();
  await userEvent.click(raise);
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("capa-99"));
});
