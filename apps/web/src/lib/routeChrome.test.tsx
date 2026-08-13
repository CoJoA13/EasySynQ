import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "./routeChrome";

function Harness() {
  useRouteChrome();
  const nav = useNavigate();
  return (
    <>
      <button onClick={() => nav("/library")}>go-library</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK")}>go-acknowledgements</button>
      <button onClick={() => nav(-1)}>go-back</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK&q=needle")}>change-search</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK&sort=title&dir=asc")}>change-sort</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK&offset=20&size=10")}>change-page</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK&future=value")}>change-unknown</button>
      <button onClick={() => nav("/tasks?q=needle&type=DOC_ACK")}>change-order</button>
      <button onClick={() => nav("/tasks?type=DOC_ACK#results")}>change-hash</button>
      <button onClick={() => nav("/library?detail=doc-a")}>open-detail</button>
      <button onClick={() => nav("/documents/doc-a?tab=history")}>change-document-tab</button>
      <main id="main-content" tabIndex={-1}>
        content
      </main>
      <RouteAnnouncement />
    </>
  );
}

function renderChrome(initialEntry: string) {
  return renderWithProviders(
    <RouteChromeProvider>
      <Harness />
    </RouteChromeProvider>,
    { route: initialEntry },
  );
}

describe("useRouteChrome", () => {
  it("sets the document title per route and focuses main on navigation (not initial mount)", async () => {
    const user = userEvent.setup();
    renderChrome("/compliance");
    // initial route → title set, but focus NOT stolen from the document body
    expect(document.title).toBe("EasySynQ — Compliance");
    expect(document.activeElement).not.toBe(document.getElementById("main-content"));

    await user.click(screen.getByText("go-library"));
    expect(document.title).toBe("EasySynQ — Library");
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });

  it("gives the dashboard root route its own title (not the bare app name)", () => {
    renderChrome("/");
    expect(document.title).toBe("EasySynQ — Dashboard");
  });

  it("does not let the root '/' entry shadow a deeper route", () => {
    renderChrome("/library");
    expect(document.title).toBe("EasySynQ — Library");
  });

  it("uses the user-facing Import name for the canonical route", () => {
    renderChrome("/imports");
    expect(document.title).toBe("EasySynQ — Import");
  });

  it.each(["/totally-unknown", "/library/not-a-real-route"])(
    "uses the not-found title for unmatched route %s",
    (route) => {
      renderChrome(route);
      expect(document.title).toBe("EasySynQ — Page not found");
    },
  );

  it("sets acknowledgement deep-link chrome without stealing focus or announcing navigation", () => {
    renderChrome("/tasks?type=DOC_ACK");
    const main = document.getElementById("main-content");

    expect(document.title).toBe("EasySynQ — Acknowledgements");
    expect(document.activeElement).not.toBe(main);
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  });

  it("treats Tasks and Acknowledgements as live route-main transitions", async () => {
    const user = userEvent.setup();
    const focusMain = vi.fn();
    renderChrome("/tasks");
    const main = document.getElementById("main-content");
    main?.addEventListener("focus", focusMain);

    expect(document.title).toBe("EasySynQ — Tasks");
    expect(document.activeElement).not.toBe(main);
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");

    await user.click(screen.getByRole("button", { name: "go-acknowledgements" }));
    expect(document.title).toBe("EasySynQ — Acknowledgements");
    expect(document.activeElement).toBe(main);
    expect(focusMain).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent(
      "Acknowledgements",
    );

    await user.click(screen.getByRole("button", { name: "go-back" }));
    expect(document.title).toBe("EasySynQ — Tasks");
    expect(document.activeElement).toBe(main);
    expect(focusMain).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("Tasks");
  });

  it.each([
    "change-search",
    "change-sort",
    "change-page",
    "change-unknown",
    "change-order",
    "change-hash",
  ])("does not claim route-main navigation for %s", async (buttonName) => {
    const user = userEvent.setup();
    const focusMain = vi.fn();
    renderChrome("/tasks?type=DOC_ACK");
    const main = document.getElementById("main-content");
    main?.addEventListener("focus", focusMain);

    await user.click(screen.getByRole("button", { name: buttonName }));

    expect(document.title).toBe("EasySynQ — Acknowledgements");
    expect(document.activeElement).not.toBe(main);
    expect(focusMain).not.toHaveBeenCalled();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  });

  it("leaves feature-owned detail focus and announcements alone", async () => {
    const user = userEvent.setup();
    const focusMain = vi.fn();
    renderChrome("/library");
    const main = document.getElementById("main-content");
    main?.addEventListener("focus", focusMain);

    await user.click(screen.getByRole("button", { name: "open-detail" }));
    expect(document.title).toBe("EasySynQ — Document details");
    expect(document.activeElement).not.toBe(main);
    expect(focusMain).not.toHaveBeenCalled();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  });

  it("leaves document-tab chrome, focus, and announcements alone", async () => {
    const user = userEvent.setup();
    const focusMain = vi.fn();
    renderChrome("/documents/doc-a");
    const main = document.getElementById("main-content");
    main?.addEventListener("focus", focusMain);

    await user.click(screen.getByRole("button", { name: "change-document-tab" }));
    expect(document.title).toBe("EasySynQ — Document");
    expect(document.activeElement).not.toBe(main);
    expect(focusMain).not.toHaveBeenCalled();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  });
});
