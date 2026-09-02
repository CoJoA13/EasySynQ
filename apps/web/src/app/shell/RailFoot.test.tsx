import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { meFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { MutationFeedbackOutlet } from "../../lib/mutationFeedback";
import { RailFoot } from "./RailFoot";
import { useColorSchemePreference } from "./useColorSchemePreference";

// R69. Spread the serializer-pinned fixture rather than hand-typing a subset — HttpResponse.json
// accepts any JSON, so a fabricated shape is invisible to both vitest and tsc.
function meWith(overrides: Record<string, unknown>) {
  server.use(http.get("/api/v1/me", () => HttpResponse.json({ ...meFixture, ...overrides })));
}

it("reflects the stored account preference rather than defaulting to the control's first option", async () => {
  // LOAD-BEARING, and DARK is chosen deliberately: it is neither the first option nor the fallback
  // the component shows before /me resolves, so a component that ignored the account value entirely
  // — rendering LIGHT or AUTO — would fail. Asserting AUTO here would have passed either way.
  meWith({ color_scheme: "DARK" });
  renderWithProviders(<RailFoot />);
  await waitFor(() => expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked());
});

it("writes the chosen scheme to the account", async () => {
  const sent: unknown[] = [];
  server.use(
    http.patch("/api/v1/me/preferences", async ({ request }) => {
      const body = await request.json();
      sent.push(body);
      return HttpResponse.json({ ...meFixture, color_scheme: "DARK" });
    }),
  );
  renderWithProviders(<RailFoot />);
  await screen.findByRole("radio", { name: "Auto" });

  await userEvent.click(screen.getByRole("radio", { name: "Dark" }));

  // The request body, not merely "a request happened" — a component that PATCHed the wrong field
  // name, or the Mantine lower-case value, would satisfy a call-count assertion.
  await waitFor(() => expect(sent).toEqual([{ color_scheme: "DARK" }]));
});

it("keeps AUTO reachable after a fixed scheme has been chosen", async () => {
  // R69 makes AUTO a real selectable value, not merely the initial one. Nothing else in the suite
  // would notice a control that let a user leave AUTO but never return to it.
  const sent: string[] = [];
  server.use(
    http.patch("/api/v1/me/preferences", async ({ request }) => {
      const body = (await request.json()) as { color_scheme: string };
      sent.push(body.color_scheme);
      return HttpResponse.json({ ...meFixture, color_scheme: body.color_scheme });
    }),
  );
  meWith({ color_scheme: "DARK" });
  renderWithProviders(<RailFoot />);
  await waitFor(() => expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked());

  await userEvent.click(screen.getByRole("radio", { name: "Auto" }));
  await waitFor(() => expect(sent).toEqual(["AUTO"]));
});

it("renders the clock on ORGANIZATION time, not the browser's", async () => {
  // The fixture's zone is deliberately far from UTC and the clock is pinned, so the expected string
  // is one only org-time formatting can produce. A browser-local clock would show whatever the
  // test host is set to; a UTC clock would show 15:00.
  // `shouldAdvanceTime` is load-bearing, not decoration: testing-library polls `findBy*` on real
  // timers, so plain `useFakeTimers()` freezes the poll and the query times out at 5s — and because
  // the fake clock outlives a failed test, it also times out the two tests that follow. Both
  // symptoms were observed before this option was added.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-06-28T15:00:00Z"));
  try {
    meWith({ org_timezone: "Asia/Tokyo" });
    renderWithProviders(<RailFoot />);
    const clock = await screen.findByLabelText("Organization time");
    expect(clock).toHaveTextContent("00:00");
  } finally {
    vi.useRealTimers();
  }
});

// The date and the ORDER are both pure-DOM facts with no behaviour attached, so nothing in the
// suite above can see either. Reverting the reorder, or dropping the date, leaves every other
// assertion in this file green — which is exactly why these two exist.
it("renders the six-digit organization date beside the time", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  // 15:00Z on 28 June is already the 29th in Tokyo, so a browser-local or UTC date would read
  // 06/28/26 and only an org-zone date reads 06/29/26.
  vi.setSystemTime(new Date("2026-06-28T15:00:00Z"));
  try {
    meWith({ org_timezone: "Asia/Tokyo" });
    renderWithProviders(<RailFoot />);
    expect(await screen.findByLabelText("Organization date")).toHaveTextContent("06/29/26");
  } finally {
    vi.useRealTimers();
  }
});

it("places the clock ABOVE the theme control", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-06-28T15:00:00Z"));
  try {
    meWith({ org_timezone: "Asia/Tokyo" });
    renderWithProviders(<RailFoot />);
    const clock = await screen.findByLabelText("Organization time");
    const control = screen.getByRole("radiogroup", { name: "Interface theme" });
    // DOCUMENT_POSITION_FOLLOWING means the control comes after the clock in document order, which
    // is the reading order a screen reader and a sighted user both get. Asserting the relationship
    // rather than an index keeps it true however the surrounding markup is nested.
    expect(clock.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

it("renders no clock at all when the organization timezone is unusable", async () => {
  // Showing the browser's time under an "Organization time" label would look authoritative and be
  // wrong, so the absence is the contract. The theme control must still render.
  //
  // ⚠ The synchronisation is load-bearing and the first draft got it wrong. It waited on the
  // SegmentedControl, which RailFoot renders unconditionally at mount with no dependency on /me —
  // so the assertion ran while `me` was still undefined, the `!timeZone` guard returned null, and
  // the "Not/AZone" fixture was never read at all. Measured: zero Intl.DateTimeFormat constructions
  // in the whole test. It has to wait on something only /me can satisfy, so the bad zone is the
  // reason for the absence rather than a query still being in flight.
  meWith({ org_timezone: "Not/AZone", color_scheme: "DARK" });
  renderWithProviders(<RailFoot />);
  await waitFor(() => expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked());
  expect(screen.queryByLabelText("Organization time")).not.toBeInTheDocument();
});

it("leaves the chosen scheme applied when the account write fails", async () => {
  // The local scheme is a legitimate browser-level preference on its own, so snapping the page back
  // would punish the user for a server problem. The control stays on the user's choice.
  server.use(
    http.patch("/api/v1/me/preferences", () =>
      HttpResponse.json({ detail: "nope" }, { status: 500 }),
    ),
  );
  renderWithProviders(<RailFoot />);
  await screen.findByRole("radio", { name: "Auto" });

  await userEvent.click(screen.getByRole("radio", { name: "Light" }));

  await waitFor(() => expect(screen.getByRole("radio", { name: "Light" })).toBeChecked());
});

it("does not let a late /me overwrite a choice made before it resolved", async () => {
  // The race that the failed-write test surfaced, pinned directly. `/me` is delayed past the click,
  // so the account value (AUTO) arrives AFTER the user has picked DARK. A reconcile that keyed only
  // on the account value changing would apply AUTO here and silently undo a deliberate action —
  // and this is not a narrow window on a cold load.
  server.use(
    http.get("/api/v1/me", async () => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      return HttpResponse.json({ ...meFixture, color_scheme: "AUTO" });
    }),
    http.patch("/api/v1/me/preferences", async ({ request }) => {
      const body = (await request.json()) as { color_scheme: string };
      return HttpResponse.json({ ...meFixture, color_scheme: body.color_scheme });
    }),
  );
  renderWithProviders(<RailFoot />);

  await userEvent.click(await screen.findByRole("radio", { name: "Dark" }));
  expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();

  // Wait past the /me delay so the reconcile has had every opportunity to fire.
  await new Promise((resolve) => setTimeout(resolve, 400));
  expect(screen.getByRole("radio", { name: "Dark" })).toBeChecked();
});

it("advances the cached /me so the account value is not left stale after a save", async () => {
  // Pins `onSuccess`'s `setQueryData`. Not redundant with the guard above: that keeps the SESSION
  // consistent, while this keeps the ["me"] cache truthful for every OTHER reader of
  // `color_scheme`. Without it the cache holds the pre-save value until something refetches.
  // A probe is needed because RailFoot renders the live preference, not the account one — the two
  // are deliberately different, so the control cannot observe this.
  server.use(
    http.get("/api/v1/me", () => HttpResponse.json({ ...meFixture, color_scheme: "AUTO" })),
    http.patch("/api/v1/me/preferences", async ({ request }) => {
      const body = (await request.json()) as { color_scheme: string };
      return HttpResponse.json({ ...meFixture, color_scheme: body.color_scheme });
    }),
  );

  function Probe() {
    const { preference, select } = useColorSchemePreference();
    return (
      <>
        <span data-testid="account">{preference ?? "-"}</span>
        <button type="button" onClick={() => select("DARK")}>
          pick dark
        </button>
      </>
    );
  }

  renderWithProviders(<Probe />);
  await waitFor(() => expect(screen.getByTestId("account")).toHaveTextContent("AUTO"));

  await userEvent.click(screen.getByRole("button", { name: "pick dark" }));

  await waitFor(() => expect(screen.getByTestId("account")).toHaveTextContent("DARK"));
});

it("reports a failed save, and a later success clears the report", async () => {
  // The failure branch's user-visible half had no coverage: the local scheme staying applied was
  // pinned, the notification that tells the user it did NOT reach their account was not. A silent
  // failure is the bad outcome here — the page looks right and the account disagrees.
  let fail = true;
  server.use(
    http.patch("/api/v1/me/preferences", async ({ request }) => {
      if (fail) return HttpResponse.json({ detail: "nope" }, { status: 500 });
      const body = (await request.json()) as { color_scheme: string };
      return HttpResponse.json({ ...meFixture, color_scheme: body.color_scheme });
    }),
  );
  // The OUTLET has to be rendered, not just the provider: `renderWithProviders` supplies the
  // context, but the entries are painted by MutationFeedbackOutlet, which normally lives in the
  // AppShell. Rendering RailFoot alone reports into a context nothing displays.
  renderWithProviders(
    <>
      <MutationFeedbackOutlet />
      <RailFoot />
    </>,
  );
  await screen.findByRole("radio", { name: "Auto" });

  await userEvent.click(screen.getByRole("radio", { name: "Dark" }));
  expect(await screen.findByText("Interface theme not saved")).toBeInTheDocument();

  // A later successful write makes the standing banner false. Without the dismiss it persists for
  // the rest of the session, still offering to retry something that has already succeeded.
  fail = false;
  await userEvent.click(screen.getByRole("radio", { name: "Light" }));
  await waitFor(() =>
    expect(screen.queryByText("Interface theme not saved")).not.toBeInTheDocument(),
  );
});

it("advances the clock as time passes", async () => {
  // Pins the tick. Deleting the interval left the clock frozen at its mount value and reddened
  // nothing — a stopped clock is wrong for 59 of every 60 minutes and looks entirely normal.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-06-28T15:00:00Z"));
  try {
    meWith({ org_timezone: "UTC" });
    renderWithProviders(<RailFoot />);
    expect(await screen.findByLabelText("Organization time")).toHaveTextContent("15:00");

    vi.setSystemTime(new Date("2026-06-28T15:16:00Z"));
    await vi.advanceTimersByTimeAsync(15_000);
    await waitFor(() =>
      expect(screen.getByLabelText("Organization time")).toHaveTextContent("15:16"),
    );
  } finally {
    vi.useRealTimers();
  }
});
