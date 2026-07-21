import { expect, test } from "vitest";
import { toDocumentFilters } from "./filters";

// R3-1 (Codex round 3, P2): `process` is a REGISTER-only facet (mapped locally in
// ReportsRegisterPage.tsx) — the SHARED toDocumentFilters (also used by the Library, whose
// FILTER_KEYS/FacetBar/hasFilters/clearFilters don't know about `process`) must never map it to
// `process_id`, or a copied/edited `/library?process=<id>` URL would silently narrow the Library by
// a hidden filter that isn't shown, counted, or cleared. Mutation-distinguishing: fails if the
// shared mapping still includes `if (uf.process) f.process_id = uf.process;`.
test("toDocumentFilters ignores the register-only process facet", () => {
  expect(toDocumentFilters({ process: "pr000001-0001-0001-0001-000000000001" })).toEqual({});
  expect(toDocumentFilters({ process: "pr000001-0001-0001-0001-000000000001" })).not.toHaveProperty(
    "process_id",
  );
});

test("toDocumentFilters still maps the Library's own five facets", () => {
  expect(
    toDocumentFilters({ state: "Effective", type: "SOP", owner: "u1", clause: "7.5.3" }),
  ).toEqual({
    current_state: "Effective",
    document_type: "SOP",
    owner_user_id: "u1",
    clause: "7.5.3",
  });
});
