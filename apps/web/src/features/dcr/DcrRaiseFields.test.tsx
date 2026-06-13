import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrRaiseFields, EMPTY_DCR_FIELDS, type DcrFieldsValue } from "./DcrRaiseFields";

const DOC = {
  id: "doc00001-0001-0001-0001-000000000001",
  identifier: "SOP-PUR-014",
  kind: "DOCUMENT",
  title: "Purchasing procedure",
  document_type_id: null,
  area_code: null,
  folder_path: null,
  current_state: "Effective",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  framework_id: "f1",
  current_effective_version_id: null,
  effective_from: null,
};

function Harness() {
  const [v, setV] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  return (
    <>
      <DcrRaiseFields value={v} onChange={setV} />
      <div data-testid="target">{v.target_document_id ?? "none"}</div>
      <div data-testid="ct">{v.change_type}</div>
    </>
  );
}

it("shows the target picker for REVISE and hides it for CREATE, clearing the target on switch", async () => {
  server.use(http.get("/api/v1/documents", () => HttpResponse.json({ data: [DOC], page: { limit: 200, offset: 0, returned: 1, total: 1 } })));
  renderWithProviders(<Harness />);
  // REVISE (default) shows the target picker.
  // Adjustment from spec: Mantine v7 Select with `required` adds an aria-hidden " *" span inside
  // the <label>, which makes the label's textContent "Target document *" — getByLabelText uses the
  // label's textContent (not the ARIA accessible name) for htmlFor associations, so the exact match
  // "Target document" fails. Use a regex to match the prefix (CapaBoardPage precedent for label quirks).
  const targetInput = screen.getByLabelText(/Target document/);
  expect(targetInput).toBeInTheDocument();
  // pick a target
  await userEvent.click(targetInput);
  await userEvent.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));
  expect(screen.getByTestId("target")).toHaveTextContent("doc00001-0001-0001-0001-000000000001");
  // switch to CREATE → picker hidden AND target cleared
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await waitFor(() => expect(screen.queryByLabelText(/Target document/)).toBeNull());
  expect(screen.getByTestId("target")).toHaveTextContent("none");
});
