import { waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import type { DocumentSummary } from "../../lib/types";
import { renderWithProviders, TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { ApprovalsTab } from "./ApprovalsTab";

const RELEASABLE = {
  current_state: "Approved" as const,
  capabilities: {
    checkout: false,
    edit: false,
    manage_metadata: false,
    submit: false,
    release: true,
    obsolete: false,
    read_draft: true,
  },
};

function doc(over: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    identifier: "SOP-PUR-014",
    kind: "DOCUMENT",
    title: "Supplier Selection",
    document_type_id: null,
    area_code: "PUR",
    folder_path: "/SOPs",
    current_state: "InReview",
    classification: "Internal",
    is_singleton: false,
    owner_user_id: "x",
    framework_id: "f",
    current_effective_version_id: null,
    effective_from: null,
    created_at: null,
    review_period_months: null,
    next_review_due: null,
    last_reviewed_at: null,
    review_state: null,
    capabilities: {
      checkout: false,
      edit: false,
      manage_metadata: false,
      submit: false,
      release: false,
      obsolete: false,
      read_draft: true,
    },
    ...over,
  };
}

test("shows the Review & approve CTA via the /me app_user id, not the OIDC sub", async () => {
  // Regression: candidate-pool membership must compare against /me.id (the app_user id =
  // candidate_pool member bbbb1111…), NOT profile.sub. Force them to differ so a profile.sub-based
  // gate would (wrongly) hide the link — the bug diff-critic caught.
  const auth = {
    ...TEST_AUTH,
    user: { profile: { sub: "9999zzzz-0000-0000-0000-000000000000" } } as typeof TEST_AUTH.user,
  };
  const { findByText, findByRole } = renderWithProviders(<ApprovalsTab doc={doc()} />, { auth });
  expect(await findByText("Quality approval")).toBeInTheDocument();
  expect(await findByRole("link", { name: /review & approve/i })).toBeInTheDocument();
});

test("shows Release when capability + Approved state", async () => {
  const { findByRole } = renderWithProviders(
    <ApprovalsTab
      doc={doc({
        current_state: "Approved",
        capabilities: {
          checkout: false,
          edit: false,
          manage_metadata: false,
          submit: false,
          release: true,
          obsolete: false,
          read_draft: true,
        },
      })}
    />,
  );
  expect(await findByRole("button", { name: "Release" })).toBeInTheDocument();
});

test("quiet-absents Release when the capability is false (DP-6)", async () => {
  const { findByText, queryByRole } = renderWithProviders(
    <ApprovalsTab doc={doc({ current_state: "Approved" })} />,
  );
  await findByText("Quality approval"); // wait for the stepper to load
  expect(queryByRole("button", { name: "Release" })).toBeNull();
});

test("#3: Release confirms first — the bare click never releases; confirming POSTs the release", async () => {
  let released = false;
  server.use(
    http.post("/api/v1/documents/:id/release", () => {
      released = true;
      return HttpResponse.json(doc({ current_state: "Effective" }));
    }),
  );
  const u = userEvent.setup();
  const { findByRole } = renderWithProviders(<ApprovalsTab doc={doc(RELEASABLE)} />);
  // The bare trigger only OPENS the confirm — it must not release yet.
  await u.click(await findByRole("button", { name: "Release" }));
  expect(released).toBe(false);
  // Confirming fires the release (proving the descriptor binds the release mutation, not another).
  await u.click(await findByRole("button", { name: "Release document" }));
  await waitFor(() => expect(released).toBe(true));
});
