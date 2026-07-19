import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useRouteChrome } from "./routeChrome";

function Harness() {
  useRouteChrome();
  const nav = useNavigate();
  return (
    <>
      <button onClick={() => nav("/library")}>go-library</button>
      <main id="main-content" tabIndex={-1}>
        content
      </main>
    </>
  );
}

describe("useRouteChrome", () => {
  it("sets the document title per route and focuses main on navigation (not initial mount)", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/compliance"]}>
        <Harness />
      </MemoryRouter>,
    );
    // initial route → title set, but focus NOT stolen from the document body
    expect(document.title).toBe("EasySynQ — Compliance");
    expect(document.activeElement).not.toBe(document.getElementById("main-content"));

    await user.click(screen.getByText("go-library"));
    expect(document.title).toBe("EasySynQ — Library");
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });

  it("falls back to the bare app name for an unmapped route", () => {
    render(
      <MemoryRouter initialEntries={["/totally-unknown"]}>
        <Harness />
      </MemoryRouter>,
    );
    expect(document.title).toBe("EasySynQ");
  });
});
