import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ackDecisionResultFixture } from "../../test/msw/handlers";
import { AckInbox } from "./AckInbox";

describe("AckInbox", () => {
  test("lists my pending DOC_ACK tasks with the document name (off the enriched list row)", async () => {
    renderWithProviders(<AckInbox />);
    // S-optimize-1: the name now comes straight off the list row (subject_identifier + subject_title),
    // no per-row detail→doc N+1. The cell renders `${identifier} — ${title}`, so match the identifier.
    expect(await screen.findByText(/SOP-PUR-014/)).toBeInTheDocument();
  });

  test("select-all + Acknowledge selected loops the POST", async () => {
    let posts = 0;
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => {
        posts += 1;
        return HttpResponse.json(ackDecisionResultFixture);
      }),
    );
    renderWithProviders(<AckInbox />);
    await screen.findByText(/SOP-PUR-014/);
    await userEvent.click(screen.getByLabelText(/select all/i));
    await userEvent.click(screen.getByRole("button", { name: /acknowledge 1 selected/i }));
    await waitFor(() => expect(posts).toBe(1));
    expect(await screen.findByText(/1 acknowledged/i)).toBeInTheDocument();
  });

  test("empty queue shows the calm empty state", async () => {
    server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
    renderWithProviders(<AckInbox />);
    expect(
      await screen.findByText(/No documents awaiting your acknowledgement/i),
    ).toBeInTheDocument();
  });
});
