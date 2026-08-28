import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense, lazy, type ReactElement } from "react";
import { MantineProvider } from "@mantine/core";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { LoadingState } from "../../lib/states";
import { theme } from "../../theme/mantine";
import { renderWithProviders } from "../../test/render";
import mainSource from "../../main.tsx?raw";

// [Audit U15] Route-level code splitting is only a win if a navigation to an UNCACHED chunk shows
// something. react-router wraps navigation in startTransition by default, and React deliberately
// keeps already-revealed content on screen rather than showing a Suspense fallback during a
// transition — so with the default the app looks FROZEN until the chunk lands. main.tsx and the
// test render therefore pass `useTransitions={false}`. This pins that pairing: it is the only
// thing making the App-level boundary's fallback reachable, and nothing else in the suite would
// notice if it were removed (chunks resolve instantly under vitest).

function slowPage(text: string, delay: number) {
  return lazy(
    () =>
      new Promise<{ default: () => ReactElement }>((resolve) =>
        setTimeout(() => resolve({ default: () => <div>{text}</div> }), delay),
      ),
  );
}

function harness(useTransitions: boolean) {
  // Fresh lazy components per harness: React.lazy MEMOIZES its payload, so a shared pair would
  // already be resolved on the second render and never suspend at all.
  const Landing = slowPage("LANDING", 0);
  const Deferred = slowPage("DEFERRED PAGE", 80);
  return (
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/landing"]} useTransitions={useTransitions}>
        <Suspense fallback={<LoadingState label="Loading page" />}>
          <Routes>
            <Route
              path="/landing"
              element={
                <>
                  <Landing />
                  <Link to="/deferred">Open deferred</Link>
                </>
              }
            />
            <Route path="/deferred" element={<Deferred />} />
          </Routes>
        </Suspense>
      </MemoryRouter>
    </MantineProvider>
  );
}

test("navigating to an uncached route chunk shows the loading state", async () => {
  render(harness(false));
  await screen.findByText("LANDING");
  await userEvent.click(screen.getByRole("link", { name: "Open deferred" }));
  expect(screen.getByRole("status", { name: "Loading page" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("DEFERRED PAGE")).toBeInTheDocument());
});

test("with react-router's default transition the fallback would never appear", async () => {
  // The behaviour this configuration exists to avoid: the previous page stays on screen with no
  // pending affordance at all. Documented here so a future `useTransitions` removal is a
  // deliberate choice rather than a silent regression.
  render(harness(true));
  await screen.findByText("LANDING");
  await userEvent.click(screen.getByRole("link", { name: "Open deferred" }));
  expect(screen.queryByRole("status", { name: "Loading page" })).toBeNull();
  expect(screen.getByText("LANDING")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("DEFERRED PAGE")).toBeInTheDocument());
});

test("the shared test render and main.tsx both opt out of router transitions", async () => {
  // The two cases above prove the SEMANTICS with their own harness — they would still pass if
  // the real router mounts silently dropped the flag. This pins the WIRING: renderWithProviders
  // supplies the MemoryRouter, so the fallback only appears if render.tsx still opts out.
  // Must be a NAVIGATION, not an initial mount: transitions only suppress fallbacks for
  // already-revealed content, so a first render shows the fallback either way.
  const Landing = slowPage("WIRED LANDING", 0);
  const Deferred = slowPage("WIRED PAGE", 80);
  renderWithProviders(
    <Suspense fallback={<LoadingState label="Loading page" />}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              <Landing />
              <Link to="/deferred">Open deferred</Link>
            </>
          }
        />
        <Route path="/deferred" element={<Deferred />} />
      </Routes>
    </Suspense>,
  );
  await screen.findByText("WIRED LANDING");
  await userEvent.click(screen.getByRole("link", { name: "Open deferred" }));
  expect(screen.getByRole("status", { name: "Loading page" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("WIRED PAGE")).toBeInTheDocument());

  // main.tsx mounts the production BrowserRouter and is not reachable from jsdom, so its flag is
  // pinned at the source level — the alternative is a silent production-only regression.
  expect(mainSource).toMatch(/<BrowserRouter useTransitions=\{false\}>/);
});
