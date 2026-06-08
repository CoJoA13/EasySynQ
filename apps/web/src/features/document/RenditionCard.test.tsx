import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RenditionCard } from "./RenditionCard";
import type { DocumentSummary } from "../../lib/types";

const doc: DocumentSummary = {
  id: "11111111-1111-1111-1111-111111111111",
  identifier: "SOP-PUR-014",
  kind: "DOCUMENT",
  title: "Supplier Selection & Evaluation",
  document_type_id: "t1",
  area_code: "PUR",
  folder_path: "/SOPs/Purchasing",
  current_state: "Effective",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "u1",
  framework_id: "f1",
  current_effective_version_id: "v1",
  effective_from: "2026-03-14T00:00:00+00:00",
  created_at: null,
  clause_refs: ["8.4"],
};

afterEach(() => vi.restoreAllMocks());

test("RenditionCard opens the controlled copy in a new tab", async () => {
  const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
  const user = userEvent.setup();
  renderWithProviders(<RenditionCard doc={doc} />);
  await user.click(screen.getByRole("button", { name: /open controlled copy/i }));
  await waitFor(() =>
    expect(openSpy).toHaveBeenCalledWith(
      "https://minio.test/cc/sop-pur-014.pdf",
      "_blank",
      "noopener,noreferrer",
    ),
  );
});

test("RenditionCard notes a source rendition (controlled PDF still rendering)", async () => {
  vi.spyOn(window, "open").mockReturnValue(null);
  server.use(
    http.get("/api/v1/documents/:id/download", () =>
      HttpResponse.json({
        download_url: "https://minio.test/staging/src.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        rendition: "source",
      }),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<RenditionCard doc={doc} />);
  await user.click(screen.getByRole("button", { name: /open controlled copy/i }));
  await waitFor(() => expect(screen.getByText(/still rendering/i)).toBeInTheDocument());
});

test("RenditionCard shows an empty state with no effective version", () => {
  renderWithProviders(
    <RenditionCard doc={{ ...doc, current_effective_version_id: null, current_state: "Draft" }} />,
  );
  expect(screen.getByText(/no governing rendition yet/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /open controlled copy/i })).not.toBeInTheDocument();
});

test("RenditionCard has no a11y violations", async () => {
  const { container } = renderWithProviders(<RenditionCard doc={doc} />);
  expect(await axe(container)).toHaveNoViolations();
});
