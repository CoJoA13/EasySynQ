import { expect, test } from "vitest";
import type { DocumentSummary } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ApprovalsTab } from "./ApprovalsTab";

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

test("shows the stepper + the Review & approve CTA for a candidate", async () => {
  const { findByText, findByRole } = renderWithProviders(<ApprovalsTab doc={doc()} />);
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
