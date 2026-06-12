import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { objectiveDetailFixture, objectiveUnderRevisionDetailFixture } from "../../test/msw/handlers";
import type { Objective, ObjectiveUpdateBody } from "../../lib/types";
import { EditCommitmentModal } from "./EditCommitmentModal";

function fill(dialog: HTMLElement, label: RegExp, value: string) {
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
}

describe("EditCommitmentModal", () => {
  it("seeds from pending_commitment when present (not the governing main fields)", async () => {
    // objectiveUnderRevisionDetailFixture has governing target "95" and pending target "97"
    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveUnderRevisionDetailFixture} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    // Must show the PENDING target "97", NOT the governing "95"
    const targetInput = within(dialog).getByLabelText(/^target/i);
    expect(targetInput).toHaveValue("97");
  });

  it("seeds from the objective fields when pending_commitment is null", async () => {
    // objectiveDetailFixture has pending_commitment: null and target_value "95"
    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveDetailFixture} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    const targetInput = within(dialog).getByLabelText(/^target/i);
    expect(targetInput).toHaveValue("95");
  });

  it("saves: change Target → PATCH carries the FULL body with the new target_value", async () => {
    let capturedBody: ObjectiveUpdateBody | null = null;
    server.use(
      http.patch("/api/v1/objectives/:id", async ({ request }) => {
        capturedBody = (await request.json()) as ObjectiveUpdateBody;
        return HttpResponse.json({ ...objectiveDetailFixture, target_value: "98" } as Objective);
      }),
    );
    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveDetailFixture} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    fill(dialog, /^target/i, "98");
    fireEvent.click(within(dialog).getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody).toMatchObject({ target_value: "98" });
    // All 7 keys must be present (full body — no omitted-field ambiguity)
    expect(capturedBody).toHaveProperty("target_value");
    expect(capturedBody).toHaveProperty("unit");
    expect(capturedBody).toHaveProperty("direction");
    expect(capturedBody).toHaveProperty("due_date");
    expect(capturedBody).toHaveProperty("at_risk_threshold");
    expect(capturedBody).toHaveProperty("baseline_value");
    expect(capturedBody).toHaveProperty("policy_id");
  });

  it("clearing the threshold field sends an explicit null (not an empty string)", async () => {
    let capturedBody: ObjectiveUpdateBody | null = null;
    server.use(
      http.patch("/api/v1/objectives/:id", async ({ request }) => {
        capturedBody = (await request.json()) as ObjectiveUpdateBody;
        return HttpResponse.json({ ...objectiveDetailFixture } as Objective);
      }),
    );
    // objectiveDetailFixture has at_risk_threshold "90" → clear it → expect null in body
    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveDetailFixture} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    // Clear the at-risk threshold field
    fill(dialog, /at-risk threshold/i, "");
    fireEvent.click(within(dialog).getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect("at_risk_threshold" in (capturedBody!)).toBe(true);
    expect(capturedBody!.at_risk_threshold).toBeNull();
  });

  it("soft-warn: backwards threshold for HIGHER_IS_BETTER warns but Save stays enabled", async () => {
    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveDetailFixture} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    // Set direction to HIGHER_IS_BETTER (already default) and set a threshold ABOVE the target
    // objectiveDetailFixture.target_value is "95"; set threshold to "99" (above target → warns)
    fill(dialog, /at-risk threshold/i, "99");

    // The BandPreview warn text appears (from labels.ts: "below the target for a "higher is better"")
    await waitFor(() =>
      expect(
        within(dialog).getByText(/at-risk threshold should be below the target/i),
      ).toBeInTheDocument(),
    );
    // Save is still enabled (warn-not-block)
    const saveBtn = within(dialog).getByRole("button", { name: /save changes/i });
    expect(saveBtn).not.toBeDisabled();
  });

  it("preserves a seeded policy link when the policy read errored", async () => {
    // Render with an objective whose policy_id is non-null so the seed carries a link.
    const objectiveWithPolicy: Objective = {
      ...objectiveDetailFixture,
      policy_id: "po000001-0001-0001-0001-000000000001",
      pending_commitment: null,
    };
    // Override the policy endpoint to 500 — the read errors out.
    server.use(
      http.get("/api/v1/objectives/policy", () =>
        HttpResponse.json({ code: "internal_error" }, { status: 500 }),
      ),
    );
    let capturedBody: ObjectiveUpdateBody | null = null;
    server.use(
      http.patch("/api/v1/objectives/:id", async ({ request }) => {
        capturedBody = (await request.json()) as ObjectiveUpdateBody;
        return HttpResponse.json({ ...objectiveWithPolicy } as Objective);
      }),
    );

    renderWithProviders(
      <EditCommitmentModal opened objective={objectiveWithPolicy} onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");

    // The neutral error copy must appear (not the positive "no policy yet")
    await waitFor(() =>
      expect(
        within(dialog).getByText(/couldn't load the quality policy/i),
      ).toBeInTheDocument(),
    );

    // Save — the body must NOT silently drop the seeded policy link to null
    fireEvent.click(within(dialog).getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody!.policy_id).toBe("po000001-0001-0001-0001-000000000001");
  });

  it("reopen resets: close unmounts; reopen seeds the original value", async () => {
    // A stateful Host component to test the conditional render (open && <Modal>) posture.
    function Host() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          {open && (
            <EditCommitmentModal
              opened
              objective={objectiveDetailFixture}
              onClose={() => setOpen(false)}
            />
          )}
        </>
      );
    }
    renderWithProviders(<Host />);

    // Open the modal
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const dialog = await screen.findByRole("dialog");

    // Dirty the Target field
    fill(dialog, /^target/i, "999");
    expect(within(dialog).getByLabelText(/^target/i)).toHaveValue("999");

    // Close (unmounts the modal)
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Reopen — should seed from the objective (target "95"), not the dirty "999"
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const dialog2 = await screen.findByRole("dialog");
    expect(within(dialog2).getByLabelText(/^target/i)).toHaveValue("95");
  });
});
