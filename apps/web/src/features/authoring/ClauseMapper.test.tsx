import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it } from "vitest";
import { clauseFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ClauseMapper } from "./ClauseMapper";

interface Mapping {
  id: string;
  document_id: string;
  clause_id: string;
  clause_number: string;
  clause_title: string;
  is_requirement_level: boolean;
  framework_id: string;
  created_at: string;
}

let mapped: Mapping[] = [];

beforeEach(() => {
  mapped = [];
  server.use(
    http.get("/api/v1/documents/:id/clause-mappings", () => HttpResponse.json(mapped)),
    http.post("/api/v1/documents/:id/clause-mappings", async ({ request, params }) => {
      const body = (await request.json()) as { clause_id: string };
      const clause = clauseFixture.find((c) => c.id === body.clause_id);
      const m: Mapping = {
        id: `cm-${body.clause_id}`,
        document_id: String(params.id),
        clause_id: body.clause_id,
        clause_number: clause?.number ?? "?",
        clause_title: clause?.title ?? "",
        is_requirement_level: false,
        framework_id: "f1",
        created_at: "2026-06-07T10:06:00+00:00",
      };
      mapped = [...mapped, m];
      return HttpResponse.json(m, { status: 201 });
    }),
    http.delete("/api/v1/documents/:id/clause-mappings/:cid", ({ params }) => {
      mapped = mapped.filter((m) => m.clause_id !== params.cid);
      return new HttpResponse(null, { status: 204 });
    }),
  );
});

it("maps a clause, shows it as a pill, then removes it", async () => {
  const { container } = renderWithProviders(<ClauseMapper documentId="d1" />);
  const user = userEvent.setup();

  expect(await screen.findByText(/no clauses mapped/i)).toBeInTheDocument();

  // open the searchable Select and pick clause 8.4
  await user.click(screen.getByRole("textbox", { name: /add a clause/i }));
  await user.click(await screen.findByText(/8\.4 — Control of external providers/));
  await user.click(screen.getByRole("button", { name: /^add$/i }));

  // the 8.4 pill appears (the GET refetch reflects the new mapping)
  const pill = await screen.findByText("8.4");
  expect(pill).toBeInTheDocument();

  // remove it via the pill's remove button → DELETE → the list empties
  const removeBtn = container.querySelector(".mantine-Pill-remove") as HTMLButtonElement;
  await user.click(removeBtn);
  await waitFor(() => expect(screen.getByText(/no clauses mapped/i)).toBeInTheDocument());
});

it("surfaces a duplicate-mapping conflict", async () => {
  server.use(
    http.post("/api/v1/documents/:id/clause-mappings", () =>
      HttpResponse.json({ code: "conflict", title: "Clause already mapped" }, { status: 409 }),
    ),
  );
  renderWithProviders(<ClauseMapper documentId="d1" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("textbox", { name: /add a clause/i }));
  await user.click(await screen.findByText(/8\.4 — Control of external providers/));
  await user.click(screen.getByRole("button", { name: /^add$/i }));
  expect(await screen.findByText(/already mapped/i)).toBeInTheDocument();
});
