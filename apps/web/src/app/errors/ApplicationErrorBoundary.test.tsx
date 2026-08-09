import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ApplicationErrorBoundary } from "./ApplicationErrorBoundary";

afterEach(() => vi.restoreAllMocks());

function BrokenPage(): never {
  throw new Error("RAW_ERROR_DETAIL_SENTINEL RAW_PATH_DETAIL_SENTINEL");
}

test("captures descendant render failures without displaying the thrown value", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);

  render(
    <ApplicationErrorBoundary
      fallback={({ onReset }) => <button onClick={onReset}>Recover safely</button>}
    >
      <BrokenPage />
    </ApplicationErrorBoundary>,
  );

  expect(screen.getByRole("button", { name: "Recover safely" })).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("RAW_ERROR_DETAIL_SENTINEL");
  expect(document.body).not.toHaveTextContent("RAW_PATH_DETAIL_SENTINEL");
});

test("explicit reset remounts only the failed descendant subtree", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  let shouldThrow = true;
  let successfulMounts = 0;

  function TransientPage() {
    if (shouldThrow) throw new Error("transient unsafe detail");
    successfulMounts += 1;
    return <h1>Recovered page</h1>;
  }

  render(
    <ApplicationErrorBoundary
      fallback={({ onReset }) => (
        <button
          onClick={() => {
            shouldThrow = false;
            onReset();
          }}
        >
          Try this page again
        </button>
      )}
    >
      <TransientPage />
    </ApplicationErrorBoundary>,
  );

  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(screen.getByRole("heading", { name: "Recovered page" })).toBeInTheDocument();
  expect(successfulMounts).toBe(1);
});

test("a changed reset key clears a captured failure without a retry loop", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  let shouldThrow = true;

  function RoutePage() {
    if (shouldThrow) throw new Error("route failed");
    return <h1>New location</h1>;
  }

  const rendered = render(
    <ApplicationErrorBoundary resetKey="/broken" fallback={() => <p>Page unavailable</p>}>
      <RoutePage />
    </ApplicationErrorBoundary>,
  );
  expect(screen.getByText("Page unavailable")).toBeInTheDocument();

  shouldThrow = false;
  rendered.rerender(
    <ApplicationErrorBoundary resetKey="/library" fallback={() => <p>Page unavailable</p>}>
      <RoutePage />
    </ApplicationErrorBoundary>,
  );

  expect(screen.getByRole("heading", { name: "New location" })).toBeInTheDocument();
  expect(screen.queryByText("Page unavailable")).not.toBeInTheDocument();
});
