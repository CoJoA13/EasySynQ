import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { MergeMenu } from "./MergeMenu";

const RID = ingestionRunFixture.id;
const A = "f0000000-0000-0000-0000-0000000000a1";
const B = "f0000000-0000-0000-0000-0000000000a4";
const C = "f0000000-0000-0000-0000-0000000000a5";

test("submitting posts file_ids + the chosen effective member + reconstruct flag, then calls onDone", async () => {
  const user = userEvent.setup();
  let body: unknown = null;
  let seenKey: string | null = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      seenKey = request.headers.get("Idempotency-Key");
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  const onDone = vi.fn();
  renderWithProviders(<MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={onDone} />);
  await user.click(screen.getByRole("button", { name: "Merge" }));
  // default effective member is the first id; choose the second instead.
  await user.click(await screen.findByRole("radio", { name: `Effective: ${B}` }));
  // The label carries the "(not available yet)" suffix — R10 reconstruction is unimplemented and an
  // opted-in run is refused at commit, so the control warns before it is ticked.
  await user.click(
    screen.getByRole("checkbox", { name: /Reconstruct revision chain \(not available yet\)/ }),
  );
  await user.click(screen.getByRole("button", { name: "Merge into one family" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(body).toEqual({
    file_ids: [A, B],
    effective_file_id: B,
    reconstruct_revision_chain: true,
  });
  expect(seenKey).not.toBeNull();
});

test("defaults the effective member to the first id and reconstruct OFF (R10)", async () => {
  const user = userEvent.setup();
  let body: unknown = null;
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  renderWithProviders(<MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />);
  await user.click(screen.getByRole("button", { name: "Merge" }));
  await user.click(await screen.findByRole("button", { name: "Merge into one family" }));
  await waitFor(() =>
    expect(body).toEqual({
      file_ids: [A, B],
      effective_file_id: A,
      reconstruct_revision_chain: false,
    }),
  );
});

test("surfaces the server detail when a merge fails", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", () =>
      HttpResponse.json(
        {
          code: "merge_conflict",
          title: "Merge conflict",
          detail: "Another reviewer changed this version family.",
        },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />);

  await user.click(screen.getByRole("button", { name: "Merge" }));
  await user.click(await screen.findByRole("button", { name: "Merge into one family" }));

  expect(
    await screen.findByText("Another reviewer changed this version family."),
  ).toBeInTheDocument();
  expect(screen.getByText("Merge failed")).toBeInTheDocument();
});

test("clears a merge failure when the selected members change", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/merge", () =>
      HttpResponse.json(
        {
          code: "merge_conflict",
          title: "Merge conflict",
          detail: "The A+B family changed on the server.",
        },
        { status: 409 },
      ),
    ),
  );
  const view = renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />,
  );
  await user.click(screen.getByRole("button", { name: "Merge" }));
  await user.click(await screen.findByRole("button", { name: "Merge into one family" }));
  expect(await screen.findByText("The A+B family changed on the server.")).toBeInTheDocument();

  view.rerender(<MergeMenu runId={RID} selectedFileIds={[A, C]} onDone={() => {}} />);

  await waitFor(() =>
    expect(screen.queryByText("The A+B family changed on the server.")).not.toBeInTheDocument(),
  );
  expect(screen.getByRole("radio", { name: `Effective: ${C}` })).toBeInTheDocument();
});

test("the trigger is disabled with under 2 selected files", () => {
  renderWithProviders(<MergeMenu runId={RID} selectedFileIds={[A]} onDone={() => {}} />);
  expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
  expect(screen.getByText("Select 2 or more files to merge.")).toBeInTheDocument();
});

test("has no axe violations (closed + open)", async () => {
  const user = userEvent.setup();
  const view = renderWithProviders(
    <MergeMenu runId={RID} selectedFileIds={[A, B]} onDone={() => {}} />,
  );
  expect(await axe(view.container)).toHaveNoViolations();
  await user.click(screen.getByRole("button", { name: "Merge" }));
  await screen.findByRole("button", { name: "Merge into one family" });
  expect(await axe(document.body)).toHaveNoViolations();
});
