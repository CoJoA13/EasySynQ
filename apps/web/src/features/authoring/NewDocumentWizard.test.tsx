import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { expect, it } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { NewDocumentWizard } from "./NewDocumentWizard";

type User = ReturnType<typeof userEvent.setup>;

function LibrarySentinel() {
  const [sp] = useSearchParams();
  return <div>Library detail: {sp.get("detail") ?? "none"}</div>;
}

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={qc}>
        <AuthContext.Provider value={TEST_AUTH}>
          <MemoryRouter initialEntries={["/library/new"]}>
            <Routes>
              <Route path="/library/new" element={<NewDocumentWizard />} />
              <Route path="/library" element={<LibrarySentinel />} />
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

async function clickContinue(user: User) {
  // re-query at click time (the clause-mappings fetch re-renders the step, invalidating a captured ref)
  await waitFor(() => expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: /^continue$/i }));
}

async function fillMetadataAndCreate(user: User) {
  await user.type(screen.getByLabelText(/title/i), "Supplier SOP");
  await user.click(screen.getByPlaceholderText(/pick a document type/i));
  await user.click(await screen.findByText(/SOP — Procedure/));
  await user.click(screen.getByRole("button", { name: /create & continue/i }));
}

it("step 1 creates the document, advances to upload, and is accessible", async () => {
  const { container } = renderWizard();
  const user = userEvent.setup();
  expect(await axe(container)).toHaveNoViolations();
  await fillMetadataAndCreate(user);
  expect(await screen.findByText(/upload the document file/i)).toBeInTheDocument();
});

it("drives create → upload → clauses → submit and lands back on the library", async () => {
  // step-3 gate: the document already has a clause mapped (so Continue is enabled).
  server.use(
    http.get("/api/v1/documents/:id/clause-mappings", () =>
      HttpResponse.json([
        {
          id: "cm1",
          document_id: "d",
          clause_id: "c84",
          clause_number: "8.4",
          clause_title: "x",
          is_requirement_level: false,
          framework_id: "f1",
          created_at: "2026-06-07T10:06:00+00:00",
        },
      ]),
    ),
  );
  const { container } = renderWizard();
  const user = userEvent.setup();

  await fillMetadataAndCreate(user);
  await screen.findByText(/upload the document file/i);

  // step 2 — check out, upload, check in
  await user.click(screen.getByRole("button", { name: /check out to edit/i }));
  await screen.findByText(/checked out by you/i);
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(fileInput, new File(["bytes"], "sop.pdf", { type: "application/pdf" }));
  await user.type(screen.getByLabelText(/change reason/i), "Initial version");
  await user.click(screen.getByRole("button", { name: /check in as draft/i }));

  // step 2 → step 3 (Continue enables once the check-in resolves)
  await clickContinue(user);

  // step 3 — clause already mapped → Continue
  await screen.findByText(/map this document/i);
  await clickContinue(user);

  // step 4 — submit for review → navigate to the library with the new doc selected
  // (exact name: the Stepper's step-4 indicator button is "Submit For review" — a regex would clash)
  await screen.findByText(/review and submit/i);
  await user.click(screen.getByRole("button", { name: "Submit for review" }));
  expect(await screen.findByText(/Library detail: 33333333/)).toBeInTheDocument();
});
