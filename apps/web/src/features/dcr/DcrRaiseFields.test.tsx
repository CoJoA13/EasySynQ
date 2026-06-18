import { http, HttpResponse } from "msw";
import { beforeEach, expect, it } from "vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DocumentSummary, DocumentsPage } from "../../lib/types";
import { DcrRaiseFields, EMPTY_DCR_FIELDS, type DcrFieldsValue } from "./DcrRaiseFields";

// Pinned to the real _document serializer (apps/api/.../api/documents.py::_document) via
// `satisfies DocumentSummary` so strict tsc enforces the shape — and the page envelope is the real
// {limit, offset, returned, has_more} (NOT the `total` the old fixture invented).
const BASE = {
  kind: "DOCUMENT",
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
  created_at: null,
  review_period_months: null,
  next_review_due: null,
  last_reviewed_at: null,
  review_state: null,
} satisfies Omit<DocumentSummary, "id" | "identifier" | "title">;

const PUR: DocumentSummary = {
  ...BASE,
  id: "doc00001-0001-0001-0001-000000000001",
  identifier: "SOP-PUR-014",
  title: "Purchasing procedure",
};
const QMS: DocumentSummary = {
  ...BASE,
  id: "doc00002-0002-0002-0002-000000000002",
  identifier: "SOP-QMS-001",
  title: "Quality manual",
};
const DOCS = [PUR, QMS];

// The picker drives GET /documents with a top-level free-text `q`; honour it server-side (substring,
// case-insensitive, over identifier+title) so the tests exercise the REAL server narrowing — never a
// client filter that would mask a wrong query shape.
function docsFor(request: Request): DocumentsPage {
  const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase();
  const data = DOCS.filter(
    (d) => q === "" || `${d.identifier} ${d.title}`.toLowerCase().includes(q),
  );
  return { data, page: { limit: 20, offset: 0, returned: data.length, has_more: false } };
}

beforeEach(() => {
  server.use(http.get("/api/v1/documents", ({ request }) => HttpResponse.json(docsFor(request))));
});

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
  renderWithProviders(<Harness />);
  // REVISE (default) shows the target picker.
  // Mantine v7 Select with `required` adds an aria-hidden " *" span inside the <label>, so its
  // textContent is "Target document *"; getByLabelText matches on textContent → use a regex prefix
  // (the CapaBoardPage/ImplementCreateDcrModal precedent for required-label quirks).
  const targetInput = screen.getByLabelText(/Target document/);
  expect(targetInput).toBeInTheDocument();
  // open the picker (empty q lists the starter page) and pick a target
  await userEvent.click(targetInput);
  await userEvent.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));
  expect(screen.getByTestId("target")).toHaveTextContent("doc00001-0001-0001-0001-000000000001");
  // switch to CREATE → picker hidden AND target cleared (and the local search/selection reset)
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await waitFor(() => expect(screen.queryByLabelText(/Target document/)).toBeNull());
  expect(screen.getByTestId("target")).toHaveTextContent("none");
});

it("hides the effective-date input for RETIRE (the backend ignores it)", async () => {
  renderWithProviders(<Harness />);
  // REVISE (default) shows the date input
  expect(screen.getByLabelText(/Proposed effective from/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("radio", { name: "Retire" }));
  await waitFor(() => expect(screen.queryByLabelText(/Proposed effective from/)).toBeNull());
});

it("narrows the picker by the debounced server `q` (a mid-identifier substring the old prefix-suggest would miss)", async () => {
  let lastQ: string | null = "(unset)";
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      lastQ = new URL(request.url).searchParams.get("q");
      return HttpResponse.json(docsFor(request));
    }),
  );
  renderWithProviders(<Harness />);
  const input = screen.getByLabelText(/Target document/);
  await userEvent.click(input);
  // empty q lists both
  expect(await screen.findByRole("option", { name: /SOP-PUR-014/ })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /SOP-QMS-001/ })).toBeInTheDocument();
  // type a fragment that lives MID-identifier — only the substring server filter can match it
  // (a prefix-only /search/suggest would have dropped SOP-PUR-014 here). The narrowing SIGNAL is
  // QMS disappearing while PUR remains — assert BOTH in one waitFor so it retries through the
  // transient states (the pre-narrow q="" list has both; the in-flight refetch blanks to a loader)
  // until the settled q="PUR" page. (PUR alone is a false gate — it's present in the q="" list too.)
  await userEvent.type(input, "PUR");
  await waitFor(() => {
    expect(screen.queryByRole("option", { name: /SOP-QMS-001/ })).toBeNull();
    expect(screen.getByRole("option", { name: /SOP-PUR-014/ })).toBeInTheDocument();
  });
  // the server received the debounced free-text q as a top-level param (not a bracketed filter)
  expect(lastQ).toBe("PUR");
});

it("keeps the selected target available after a non-matching search (the unioned option never desyncs)", async () => {
  renderWithProviders(<Harness />);
  const input = screen.getByLabelText(/Target document/);
  await userEvent.click(input);
  await userEvent.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));
  expect(screen.getByTestId("target")).toHaveTextContent("doc00001-0001-0001-0001-000000000001");
  // search for a term matching NEITHER document → the server page comes back empty…
  await userEvent.click(input);
  await userEvent.clear(input);
  await userEvent.type(input, "zzz");
  await waitFor(() => expect(screen.queryByRole("option", { name: /SOP-QMS-001/ })).toBeNull());
  // …but the committed target is unchanged and the picked option is still offered (unioned in), so
  // its label can always resolve and value never desyncs.
  expect(screen.getByTestId("target")).toHaveTextContent("doc00001-0001-0001-0001-000000000001");
  expect(await screen.findByRole("option", { name: /SOP-PUR-014/ })).toBeInTheDocument();
});
