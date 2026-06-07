import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CheckInPanel } from "./CheckInPanel";

const DOC = "33333333-3333-3333-3333-333333333333";

it("checks out, uploads, and checks in a new version (PUT carries no bearer)", async () => {
  let putAuth: string | null = "unset";
  server.use(
    http.put(/^https:\/\/minio\.test\//, ({ request }) => {
      putAuth = request.headers.get("authorization");
      return new HttpResponse(null, { status: 200 });
    }),
  );
  const onCheckedIn = vi.fn();
  const { container } = renderWithProviders(
    <CheckInPanel documentId={DOC} onCheckedIn={onCheckedIn} />,
  );
  const user = userEvent.setup();

  await user.click(screen.getByRole("button", { name: /check out to edit/i }));
  await screen.findByText(/checked out by you/i);

  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(fileInput, new File(["hello world"], "doc.pdf", { type: "application/pdf" }));
  await user.type(screen.getByLabelText(/change reason/i), "Initial version");
  await user.click(screen.getByRole("button", { name: /check in as draft/i }));

  await waitFor(() => expect(onCheckedIn).toHaveBeenCalledTimes(1));
  expect(putAuth).toBeNull();
  // after check-in the lock is released server-side → the panel returns to the check-out affordance
  // (so an iterative second revision in the drawer re-checks-out instead of 409-ing)
  expect(await screen.findByRole("button", { name: /check out to edit/i })).toBeInTheDocument();
});

it("disables check-in until a file and a change reason are provided", async () => {
  renderWithProviders(<CheckInPanel documentId={DOC} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /check out to edit/i }));
  const checkin = await screen.findByRole("button", { name: /check in as draft/i });
  expect(checkin).toBeDisabled();
});

it("shows the lock holder + a force-unlock affordance on a 409 lock conflict", async () => {
  server.use(
    http.post("/api/v1/documents/:id/checkout", () =>
      HttpResponse.json(
        { code: "lock_conflict", title: "Locked", detail: "checked out by Priya" },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<CheckInPanel documentId={DOC} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /check out to edit/i }));
  expect(await screen.findByText(/checked out by priya/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /force unlock/i })).toBeInTheDocument();
});

it("skips the MinIO PUT when init-upload reports a dedup hit", async () => {
  let putCalled = false;
  server.use(
    http.post(/\/api\/v1\/documents\/[^/]+\/versions:init-upload$/, () =>
      HttpResponse.json({ dedup: true, object_key: "sha-existing", upload_url: null }),
    ),
    http.put(/^https:\/\/minio\.test\//, () => {
      putCalled = true;
      return new HttpResponse(null, { status: 200 });
    }),
  );
  const onCheckedIn = vi.fn();
  const { container } = renderWithProviders(
    <CheckInPanel documentId={DOC} onCheckedIn={onCheckedIn} />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /check out to edit/i }));
  await screen.findByText(/checked out by you/i);
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(fileInput, new File(["dup"], "doc.pdf", { type: "application/pdf" }));
  await user.type(screen.getByLabelText(/change reason/i), "no-op");
  await user.click(screen.getByRole("button", { name: /check in as draft/i }));
  await waitFor(() => expect(onCheckedIn).toHaveBeenCalled());
  expect(putCalled).toBe(false);
});
