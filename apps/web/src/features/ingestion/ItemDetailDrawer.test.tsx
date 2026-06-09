import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import {
  ingestionFileDetailFixture,
  ingestionRunFixture,
} from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ItemDetailDrawer } from "./ItemDetailDrawer";

const RID = ingestionRunFixture.id;
const FID = ingestionFileDetailFixture.id;

function noop() {}

test("renders nothing actionable when fileId is null (drawer closed)", () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={null}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // No detail dialog is shown for a null file.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("renders the filename, identifier, and a classification evidence explanation", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  expect(
    await screen.findByText("SOP-PUR-014 Purchasing.docx"),
  ).toBeInTheDocument();
  // The effective review identifier surfaces (the DETAIL endpoint nests review under `effective`).
  expect(screen.getAllByText(/SOP-PUR-014/).length).toBeGreaterThan(0);
  // The classification dimension/explanation list is present (evidence array, guarded for null).
  expect(screen.getByText(/preserved/i)).toBeInTheDocument();
});

test("renders the version-family / dedup membership", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // ingestionFileDetailFixture.dedup.in_version_family === true → membership copy shows.
  expect(await screen.findByText(/version family/i)).toBeInTheDocument();
});

test("renders the extraction status (page count) and the proposal target path", async () => {
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // extract.page_count === 3
  expect(await screen.findByText(/3 pages/i)).toBeInTheDocument();
  // proposal.target_ia_path
  expect(screen.getByText(/DO\/08-Operation/)).toBeInTheDocument();
});

test("clicking Accept calls onDecision with action \"accept\"", async () => {
  const user = userEvent.setup();
  const onDecision = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={onDecision}
      onSplit={noop}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Accept item" }));
  expect(onDecision).toHaveBeenCalledWith({ action: "accept" });
});

test("Confirm kind offers Document AND Record, firing the right kind", async () => {
  const user = userEvent.setup();
  const onConfirmKind = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={onConfirmKind}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // The kind-confirm is a Menu (R10 — always-human, both Document and Record). Choose Record first.
  await user.click(await screen.findByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Record" }));
  expect(onConfirmKind).toHaveBeenLastCalledWith("RECORD");
  // Then Document.
  await user.click(screen.getByRole("button", { name: "Confirm kind" }));
  await user.click(await screen.findByRole("menuitem", { name: "Document" }));
  expect(onConfirmKind).toHaveBeenLastCalledWith("DOCUMENT");
});

test("the Split control shows for a grouped file and calls onSplit", async () => {
  const user = userEvent.setup();
  const onSplit = vi.fn();
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={onSplit}
    />,
  );
  const split = await screen.findByRole("button", { name: "Split out of group" });
  await user.click(split);
  expect(onSplit).toHaveBeenCalledTimes(1);
});

test("the Split control is hidden for an ungrouped file", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id/files/:fid", () =>
      HttpResponse.json({
        ...ingestionFileDetailFixture,
        dedup: {
          in_exact_cluster: false,
          in_near_cluster: false,
          is_canonical: null,
          redundant_of_file_id: null,
          in_version_family: false,
          is_effective: null,
          superseded_by_file_id: null,
        },
      }),
    ),
  );
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(screen.queryByRole("button", { name: "Split out of group" })).not.toBeInTheDocument();
});

test("renders the decision history from the detail response (no separate /decisions fetch)", async () => {
  // The detail endpoint already returns THIS file's history under review.decision_history; the drawer
  // reads it from there (no run-wide /decisions GET). A failing /decisions handler proves the drawer
  // never calls it (this test would still pass).
  server.use(
    http.get("/api/v1/admin/imports/:id/decisions", () =>
      HttpResponse.json({ message: "should not be called" }, { status: 500 }),
    ),
    http.get("/api/v1/admin/imports/:id/files/:fid", () =>
      HttpResponse.json({
        ...ingestionFileDetailFixture,
        review: {
          effective: ingestionFileDetailFixture.review.effective,
          decision_history: [
            {
              id: "d1",
              action: "accept",
              file_id: FID,
              cluster_id: null,
              target_kind: "DOCUMENT",
              before: null,
              after: { kind: "DOCUMENT" },
              reason: null,
              decided_by: "bbbb1111-1111-1111-1111-111111111111",
              decided_at: "2026-06-08T11:00:00+00:00",
            },
          ],
        },
      }),
    ),
  );
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  // This file's "accept" decision is listed from the detail's decision_history.
  expect(await screen.findByText(/accept/)).toBeInTheDocument();
});

test("a non-candidate file is inspect-only — no decision/confirm/split actions", async () => {
  // included_candidate === false (quarantine / scan-excluded) → every decision would 422, so the
  // drawer hides Accept/Exclude/Defer/Confirm-kind/Split and shows an inspect-only note. The
  // classification/evidence display is kept.
  server.use(
    http.get("/api/v1/admin/imports/:id/files/:fid", () =>
      HttpResponse.json({
        ...ingestionFileDetailFixture,
        included_candidate: false,
        scan_flags: { disposition: "quarantine", reason: "sniff_failed" },
      }),
    ),
  );
  renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  expect(await screen.findByText(/not a commit candidate/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Accept item" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Exclude item" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Defer item" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm kind" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Split out of group" })).not.toBeInTheDocument();
  // The classification evidence is still shown (inspect-only, not hidden).
  expect(screen.getByText(/preserved/i)).toBeInTheDocument();
});

test("has no axe violations when open", async () => {
  const { container } = renderWithProviders(
    <ItemDetailDrawer
      runId={RID}
      fileId={FID}
      onClose={noop}
      onConfirmKind={noop}
      onDecision={noop}
      onSplit={noop}
    />,
  );
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(await axe(container)).toHaveNoViolations();
});
