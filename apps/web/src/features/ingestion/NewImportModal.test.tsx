import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { NewImportModal } from "./NewImportModal";

test("submitting a source_root posts the body and calls onCreated with the new run id", async () => {
  const user = userEvent.setup();
  let seenBody: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports", async ({ request }) => {
      seenBody = await request.json();
      return HttpResponse.json({ ...ingestionRunFixture, status: "Created" }, { status: 202 });
    }),
  );
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(<NewImportModal opened onClose={onClose} onCreated={onCreated} />);

  await user.type(screen.getByLabelText("Source folder path"), "/srv/import/legacy-qms-share");
  await user.click(screen.getByLabelText("Run OCR on scanned files"));
  await user.click(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(ingestionRunFixture.id));
  expect(onClose).toHaveBeenCalled();
  expect(seenBody).toEqual({
    source_root: "/srv/import/legacy-qms-share",
    ocr_enabled: true,
  });
});

test("the Start button is disabled until a source folder is typed", async () => {
  const user = userEvent.setup();
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  expect(screen.getByRole("button", { name: "Start import" })).toBeDisabled();
  await user.type(screen.getByLabelText("Source folder path"), "/srv/x");
  expect(screen.getByRole("button", { name: "Start import" })).toBeEnabled();
});

test("a 409 (a scan is already active) renders a calm inline message — no crash", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports", () =>
      HttpResponse.json(
        { code: "active_run", title: "An import is already in progress", active_run_id: "x" },
        { status: 409 },
      ),
    ),
  );
  const onCreated = vi.fn();
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={onCreated} />);
  await user.type(screen.getByLabelText("Source folder path"), "/srv/import/x");
  await user.click(screen.getByRole("button", { name: "Start import" }));
  expect(await screen.findByText(/An import is already in progress/)).toBeInTheDocument();
  expect(onCreated).not.toHaveBeenCalled();
});

test("a 422 (bad source root) renders the returned detail calmly", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports", () =>
      HttpResponse.json(
        { code: "bad_source_root", title: "Invalid path", detail: "source_root escapes the import mount" },
        { status: 422 },
      ),
    ),
  );
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  await user.type(screen.getByLabelText("Source folder path"), "/etc/passwd");
  await user.click(screen.getByRole("button", { name: "Start import" }));
  expect(await screen.findByText(/escapes the import mount/)).toBeInTheDocument();
});

test("has no axe violations when open", async () => {
  renderWithProviders(<NewImportModal opened onClose={() => {}} onCreated={() => {}} />);
  await screen.findByLabelText("Source folder path");
  expect(await axe(document.body)).toHaveNoViolations();
});
