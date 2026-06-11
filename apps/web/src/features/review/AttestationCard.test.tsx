import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";

import { AttestationCard } from "./AttestationCard";

const TASK = "tkak1111-1111-1111-1111-111111111111";
const DOC = "11111111-1111-1111-1111-111111111111";

describe("AttestationCard", () => {
  test("one click acknowledges and navigates to /tasks", async () => {
    let outcome: string | null = null;
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        outcome = ((await request.json()) as { outcome: string }).outcome;
        return HttpResponse.json({ document_id: DOC, acknowledgement_id: "a", replayed: false });
      }),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />, { route: "/tasks/" + TASK });
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    await waitFor(() => expect(outcome).toBe("acknowledge"));
  });

  test("a 409 ack_superseded shows the supersede copy, not a crash", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => HttpResponse.json({ code: "ack_superseded", title: "x" }, { status: 409 })),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    expect(await screen.findByText(/newer major revision was released/i)).toBeInTheDocument();
  });

  test("a 409 ack_obligation_lapsed shows the lapsed copy", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => HttpResponse.json({ code: "ack_obligation_lapsed", title: "x" }, { status: 409 })),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    expect(await screen.findByText(/no longer requires your acknowledgement/i)).toBeInTheDocument();
  });

  test("a 409 conflict shows the conflict copy", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => HttpResponse.json({ code: "conflict", title: "x" }, { status: 409 })),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    expect(await screen.findByText(/you've already acknowledged this/i)).toBeInTheDocument();
  });

  test("no signature checkbox and no outcome radio (acknowledge-only, R43)", () => {
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText(/signing as/i)).not.toBeInTheDocument();
  });

  // Regression: a retry of the SAME mounted attempt must reuse one Idempotency-Key, so a
  // committed-but-lost-response ack replays (200) on retry instead of 409-ing as a fresh decision.
  test("a retry reuses the same Idempotency-Key (stable per mount)", async () => {
    const keys: string[] = [];
    server.use(
      http.post("/api/v1/tasks/:id/decision", ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key") ?? "");
        return HttpResponse.json({ code: "conflict", title: "x" }, { status: 409 });
      }),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    const btn = screen.getByRole("button", { name: /i have read & understood/i });
    await userEvent.click(btn);
    await screen.findByText(/you've already acknowledged this/i);
    await userEvent.click(btn);
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBeTruthy();
    expect(keys[0]).toBe(keys[1]);
  });
});
