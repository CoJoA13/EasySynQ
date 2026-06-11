import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { TopBar } from "./TopBar";

function renderBar() {
  return renderWithProviders(<TopBar navOpened={false} onToggleNav={() => {}} onOpenSearch={() => {}} />, { route: "/" });
}

describe("TopBar ack bell", () => {
  test("the bell links to the filtered DOC_ACK inbox and keeps a distinct aria-label", async () => {
    renderBar();
    const link = await screen.findByRole("link", { name: "Acknowledgements" });
    expect(link).toHaveAttribute("href", "/tasks?type=DOC_ACK&state=PENDING");
    expect(screen.getByLabelText("Tasks")).toBeInTheDocument(); // sibling untouched, distinct label
  });

  test("shows the open-DOC_ACK count badge", async () => {
    server.use(
      http.get("/api/v1/tasks", ({ request }) => {
        const type = new URL(request.url).searchParams.get("type");
        return HttpResponse.json(type === "DOC_ACK" ? [{ id: "a" }, { id: "b" }, { id: "c" }] : []);
      }),
    );
    renderBar();
    expect(await screen.findByText("3")).toBeInTheDocument();
  });
});
