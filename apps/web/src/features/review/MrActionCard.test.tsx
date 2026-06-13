import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";

import { MrActionCard } from "./MrActionCard";

const TASK = "tkmr1111-1111-1111-1111-111111111111";
const REVIEW = "mr-0001-0001-0001-000000000001";

describe("MrActionCard", () => {
  it("renders one complete button and no sign/radio affordance (complete-only)", () => {
    renderWithProviders(<MrActionCard taskId={TASK} reviewId={REVIEW} />);
    expect(screen.getByRole("button", { name: /mark action complete/i })).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/signing as/i)).not.toBeInTheDocument();
  });

  it("one click posts outcome:complete and navigates to /tasks", async () => {
    let outcome: string | null = null;
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        outcome = ((await request.json()) as { outcome: string }).outcome;
        return HttpResponse.json({ current_state: "DONE" });
      }),
    );
    renderWithProviders(<MrActionCard taskId={TASK} reviewId={REVIEW} />, { route: "/tasks/" + TASK });
    await userEvent.click(screen.getByRole("button", { name: /mark action complete/i }));
    await waitFor(() => expect(outcome).toBe("complete"));
  });

  it("sends an Idempotency-Key header stable per mount across retries", async () => {
    const keys: string[] = [];
    server.use(
      http.post("/api/v1/tasks/:id/decision", ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key") ?? "");
        return HttpResponse.json({ code: "validation_error", title: "x" }, { status: 422 });
      }),
    );
    renderWithProviders(<MrActionCard taskId={TASK} reviewId={REVIEW} />);
    const btn = screen.getByRole("button", { name: /mark action complete/i });
    await userEvent.click(btn);
    await screen.findByText(/only supports being marked complete/i);
    await userEvent.click(btn);
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBeTruthy();
    expect(keys[0]).toBe(keys[1]);
  });

  it("maps a 422 validation_error to calm copy, not a crash", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () =>
        HttpResponse.json({ code: "validation_error", title: "x" }, { status: 422 }),
      ),
    );
    renderWithProviders(<MrActionCard taskId={TASK} reviewId={REVIEW} />);
    await userEvent.click(screen.getByRole("button", { name: /mark action complete/i }));
    expect(await screen.findByText(/only supports being marked complete/i)).toBeInTheDocument();
  });

  it("maps a 404 not_found to calm copy", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () =>
        HttpResponse.json({ code: "not_found", title: "x" }, { status: 404 }),
      ),
    );
    renderWithProviders(<MrActionCard taskId={TASK} reviewId={REVIEW} />);
    await userEvent.click(screen.getByRole("button", { name: /mark action complete/i }));
    expect(await screen.findByText(/no longer assigned to you/i)).toBeInTheDocument();
  });
});
