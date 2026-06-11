import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useNavigate } from "react-router-dom";
import { describe, expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { TasksInbox } from "./TasksInbox";

test("lists pending tasks with a link to the review page", async () => {
  const { findByRole } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  const link = await findByRole("link", { name: /approve/i });
  expect(link).toHaveAttribute("href", "/tasks/task1111-1111-1111-1111-111111111111");
});

test("shows a calm empty state", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  const { findByText } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByText("No tasks in your queue.")).toBeInTheDocument();
});

test("surfaces a 403 quietly", async () => {
  server.use(
    http.get("/api/v1/tasks", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { findByText } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByText(/don't have access/i)).toBeInTheDocument();
});

describe("TasksInbox routing", () => {
  test("?type=DOC_ACK renders the AckInbox", async () => {
    renderWithProviders(<TasksInbox />, { route: "/tasks?type=DOC_ACK" });
    expect(await screen.findByRole("heading", { name: "Acknowledgements" })).toBeInTheDocument();
  });

  test("no type param renders the default review queue", async () => {
    renderWithProviders(<TasksInbox />, { route: "/tasks" });
    expect(await screen.findByText("Review & Approve")).toBeInTheDocument();
  });

  // Regression: `/tasks` and `/tasks?type=DOC_ACK` are the SAME route element, so the bell→inbox
  // navigation transitions the param on an ALREADY-MOUNTED TasksInbox. The dispatcher must keep an
  // invariant hook count across that transition (the earlier conditional-return-before-useTasks shape
  // threw "Rendered fewer hooks than expected"). Render TasksInbox directly (not via a Route) so the
  // single instance survives both navigations.
  test("a live ?type transition on a mounted inbox does not violate Rules-of-Hooks", async () => {
    function TransitionHarness() {
      const nav = useNavigate();
      return (
        <>
          <button onClick={() => nav("/tasks?type=DOC_ACK")}>to-ack</button>
          <button onClick={() => nav("/tasks")}>to-general</button>
          <TasksInbox />
        </>
      );
    }
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithProviders(<TransitionHarness />, { route: "/tasks" });
    expect(await screen.findByText("Review & Approve")).toBeInTheDocument();
    await userEvent.click(screen.getByText("to-ack"));
    expect(await screen.findByRole("heading", { name: "Acknowledgements" })).toBeInTheDocument();
    await userEvent.click(screen.getByText("to-general"));
    expect(await screen.findByText("Review & Approve")).toBeInTheDocument();
    expect(errSpy.mock.calls.flat().join(" ")).not.toMatch(/Rendered (fewer|more) hooks/);
    errSpy.mockRestore();
  });
});
