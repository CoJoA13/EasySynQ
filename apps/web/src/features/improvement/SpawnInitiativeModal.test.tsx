import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useRaiseInitiativeFromFinding, useRaiseInitiativeFromMrOutput } from "./mutations";
import { SpawnInitiativeModal } from "./SpawnInitiativeModal";

// A tiny host that binds the real spawn hook and renders the generic modal — exercises the modal
// against the MSW handlers exactly the way the FindingsCard / ReviewOutputsSection parents do.
function FindingHost({
  onCreated,
  onClose,
}: {
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const mutation = useRaiseInitiativeFromFinding("fd000002-0002-0002-0002-000000000002");
  return (
    <SpawnInitiativeModal
      heading="Raise an improvement initiative from this finding"
      mutation={mutation}
      onClose={onClose}
      onCreated={onCreated}
    />
  );
}

function MrHost({ onCreated, onClose }: { onCreated: (id: string) => void; onClose: () => void }) {
  const mutation = useRaiseInitiativeFromMrOutput(
    "mr000001-0001-0001-0001-000000000001",
    "out00001-0001-0001-0001-000000000001",
  );
  return (
    <SpawnInitiativeModal
      heading="Raise an improvement initiative from this output"
      mutation={mutation}
      showProcessPicker
      onClose={onClose}
      onCreated={onCreated}
    />
  );
}

describe("SpawnInitiativeModal", () => {
  it("disables Raise until a title is typed", async () => {
    const u = userEvent.setup();
    renderWithProviders(<FindingHost onCreated={() => {}} onClose={() => {}} />);
    const raise = screen.getByRole("button", { name: "Raise" });
    expect(raise).toBeDisabled();
    // exact required-field label (the auto-formatter keeps "Title" distinct from "Target outcome").
    await u.type(screen.getByLabelText(/^Title/), "Automate the scorecard");
    expect(raise).toBeEnabled();
  });

  it("POSTs with an Idempotency-Key, fires onCreated + onClose on success", async () => {
    let seenKey: string | null = null;
    server.use(
      http.post("/api/v1/findings/:findingId/raise-initiative", async ({ request }) => {
        seenKey = request.headers.get("Idempotency-Key");
        const body = (await request.json()) as { title: string };
        return HttpResponse.json(
          {
            id: "10000000-0000-0000-0000-0000000000f1",
            identifier: "IMP-2026-0010",
            title: body.title,
            description: null,
            target_outcome: null,
            source: "OFI",
            source_link_id: "fd000002-0002-0002-0002-000000000002",
            process_id: null,
            owner_user_id: null,
            stage: "Open",
            opened_at: "2026-06-17T09:00:00Z",
            closed_at: null,
            created_by: "20000000-0000-0000-0000-0000000000aa",
            created_at: "2026-06-17T09:00:00Z",
            updated_at: null,
          },
          { status: 201 },
        );
      }),
    );
    const onCreated = vi.fn();
    const onClose = vi.fn();
    const u = userEvent.setup();
    renderWithProviders(<FindingHost onCreated={onCreated} onClose={onClose} />);
    await u.type(screen.getByLabelText(/^Title/), "Automate the scorecard");
    await u.click(screen.getByRole("button", { name: "Raise" }));
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith("10000000-0000-0000-0000-0000000000f1"),
    );
    expect(onClose).toHaveBeenCalled();
    expect(seenKey).toBeTruthy();
  });

  it("offers the Process picker only when showProcessPicker is set", async () => {
    // The MR host (showProcessPicker) renders the picker once the processes list resolves.
    renderWithProviders(<MrHost onCreated={() => {}} onClose={() => {}} />);
    expect(await screen.findByLabelText("Process (optional)")).toBeInTheDocument();
  });

  it("omits the Process picker for the finding seam (no showProcessPicker)", async () => {
    renderWithProviders(<FindingHost onCreated={() => {}} onClose={() => {}} />);
    // The owner picker (always offered) resolves once the directory loads — wait on it, then assert
    // the process picker is absent (so we don't assert absence before the async data settles).
    await screen.findByLabelText("Owner (optional)");
    expect(screen.queryByLabelText("Process (optional)")).toBeNull();
  });

  it("renders an API error calmly in the Alert and does NOT fire onCreated", async () => {
    server.use(
      http.post("/api/v1/findings/:findingId/raise-initiative", () =>
        HttpResponse.json(
          {
            code: "finding_not_improvable",
            title: "Not improvable",
            detail: "This finding type cannot raise an initiative.",
          },
          { status: 422 },
        ),
      ),
    );
    const onCreated = vi.fn();
    const u = userEvent.setup();
    renderWithProviders(<FindingHost onCreated={onCreated} onClose={() => {}} />);
    await u.type(screen.getByLabelText(/^Title/), "Bad raise");
    await u.click(screen.getByRole("button", { name: "Raise" }));
    expect(
      await screen.findByText("This finding type cannot raise an initiative."),
    ).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("renders a 409 conflict calmly without crashing", async () => {
    server.use(
      http.post("/api/v1/findings/:findingId/raise-initiative", () =>
        HttpResponse.json(
          {
            code: "finding_superseded",
            title: "Superseded",
            detail: "This finding was superseded by a correction.",
          },
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderWithProviders(<FindingHost onCreated={() => {}} onClose={() => {}} />);
    await u.type(screen.getByLabelText(/^Title/), "Late raise");
    await u.click(screen.getByRole("button", { name: "Raise" }));
    expect(
      await screen.findByText("This finding was superseded by a correction."),
    ).toBeInTheDocument();
  });
});
