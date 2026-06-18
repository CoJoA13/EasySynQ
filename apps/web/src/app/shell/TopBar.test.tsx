import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { TopBar } from "./TopBar";

function renderBar() {
  return renderWithProviders(
    <TopBar navOpened={false} onToggleNav={() => {}} onOpenSearch={() => {}} />,
    { route: "/" },
  );
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

  test("a genuine zero is silent — no badge, plain aria-label", async () => {
    server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
    renderBar();
    // the plain-named bell stays, but there is no numeric badge for zero
    expect(await screen.findByRole("link", { name: "Acknowledgements" })).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  test("a failed count shows an indeterminate bell — never a confident 0 (the silent-zero fix)", async () => {
    server.use(
      http.get("/api/v1/tasks", ({ request }) => {
        const type = new URL(request.url).searchParams.get("type");
        if (type === "DOC_ACK") return new HttpResponse(null, { status: 500 });
        return HttpResponse.json([]);
      }),
    );
    renderBar();
    // the bell names itself unavailable, and no "0" masquerades as "no acks"
    expect(
      await screen.findByRole("link", { name: "Acknowledgements (count unavailable)" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Acknowledgements" })).not.toBeInTheDocument();
  });
});
