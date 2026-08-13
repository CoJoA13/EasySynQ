import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { screen, waitFor, within } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import type { DistributionPayload } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import {
  ackMatrixFixture,
  detailCapabilities,
  distributionFixture,
  distributionNoEffectiveFixture,
  docFixture,
} from "../../test/msw/handlers";
import { DocumentDetailPage } from "./DocumentDetailPage";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";

const ID = "11111111-1111-1111-1111-111111111111";

function renderPage(route = `/documents/${ID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="documents/:id" element={<DocumentDetailPage />} />
    </Routes>,
    { route },
  );
}

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output aria-label="Current location">{pathname + search}</output>;
}

function DocumentTabNavigation() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/documents/${ID}?tab=history`)}>external history</button>
      <button onClick={() => navigate(`/documents/${ID}?tab=approvals`)}>external approvals</button>
      <button onClick={() => navigate(`/documents/${ID}?tab=where-used`)}>
        external where-used
      </button>
      <button onClick={() => navigate(`/documents/${ID}?tab=acks`)}>external acks</button>
      <button onClick={() => navigate(`/documents/${ID}?tab=unknown-sentinel`)}>
        external unknown
      </button>
      <button onClick={() => navigate(`/documents/${ID}`)}>external overview</button>
      <DocumentDetailPage />
    </>
  );
}

function DocumentWithRouteChrome() {
  useRouteChrome();
  return (
    <main id="main-content" tabIndex={-1}>
      <RouteAnnouncement />
      <DocumentDetailPage />
    </main>
  );
}

function DocumentHistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/documents/${ID}?sentinel=keep&checkpoint=prepared`)}>
        Prepare history
      </button>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}

// Re-serve the detail doc with an overridden capabilities block (for the author-gating tests).
function serveDocWithCaps(caps: Partial<typeof detailCapabilities>) {
  server.use(
    http.get("/api/v1/documents/:id", ({ params }) => {
      const doc = docFixture.find((d) => d.id === params.id);
      return doc
        ? HttpResponse.json({ ...doc, capabilities: { ...detailCapabilities, ...caps } })
        : HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 });
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

test("DocumentDetailPage renders the header, tiles, rendition and metadata (Overview tab)", async () => {
  renderPage();
  expect(
    await screen.findByRole("heading", { name: "Supplier Selection & Evaluation" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Governing revision")).toBeInTheDocument();
  expect(screen.getByText("Mapped clauses")).toBeInTheDocument();
  expect(screen.getByText("Versions")).toBeInTheDocument();
  // governing revision resolves from the version list (current_effective_version_id → Rev B)
  await waitFor(() => expect(screen.getAllByText("Rev B").length).toBeGreaterThanOrEqual(1));
  // Overview tab (default): the rendition + control metadata.
  expect(screen.getByText("Controlled rendition")).toBeInTheDocument();
  expect(screen.getByText("Control metadata")).toBeInTheDocument();
});

test("DocumentDetailPage shows Version history under the History tab", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  await userEvent.click(screen.getByRole("tab", { name: /history/i }));
  expect(await screen.findByText("Version history")).toBeInTheDocument();
});

test("DocumentDetailPage shows Where-used under its tab", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  await userEvent.click(screen.getByRole("tab", { name: /where-used/i }));
  // unique WhereUsedTab content (from the fixture) appears only when the panel is active.
  expect(await screen.findByText("Records produced under")).toBeInTheDocument();
});

test("DocumentDetailPage renders the Approvals stepper card under its tab", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  await userEvent.click(screen.getByRole("tab", { name: /approvals/i }));
  expect(await screen.findByText("Quality approval")).toBeInTheDocument();
});

test("DocumentDetailPage shows a loading skeleton before the document resolves", () => {
  renderPage();
  expect(screen.getByLabelText("Loading document")).toBeInTheDocument();
});

test("DocumentDetailPage shows a not-found state for a missing document (404)", async () => {
  renderPage("/documents/99999999-9999-9999-9999-999999999999");
  expect(await screen.findByText("This document does not exist.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to the Library/ })).toBeInTheDocument();
});

test("DocumentDetailPage shows a no-access state on a 403", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to this document.")).toBeInTheDocument();
});

test("DocumentDetailPage uses the canonical retry copy and refetches after a generic failure", async () => {
  let attempts = 0;
  server.use(
    http.get("/api/v1/documents/:id", ({ params }) => {
      attempts += 1;
      if (attempts === 1) {
        return HttpResponse.json({ code: "error", title: "Error" }, { status: 500 });
      }
      const doc = docFixture.find((d) => d.id === params.id);
      return HttpResponse.json(doc);
    }),
  );
  const user = userEvent.setup();
  renderPage();
  expect(await screen.findByText("Couldn't load this document")).toBeInTheDocument();
  expect(screen.getByText("Please try again.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to the Library/ })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: "Supplier Selection & Evaluation" }),
  ).toBeInTheDocument();
  expect(attempts).toBe(2);
});

test("DocumentDetailPage hides author actions without the edit capability (DP-6)", async () => {
  renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  expect(screen.queryByRole("button", { name: /Start revision/ })).not.toBeInTheDocument();
});

test("DocumentDetailPage shows Start revision when the edit capability is present", async () => {
  serveDocWithCaps({ edit: true });
  renderPage();
  expect(await screen.findByRole("button", { name: /Start revision/ })).toBeInTheDocument();
});

test("DocumentDetailPage has no a11y violations (read-only)", async () => {
  const { container } = renderPage();
  await screen.findByRole("heading", { name: /Supplier Selection/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("DocumentDetailPage has no a11y violations (with author actions)", async () => {
  serveDocWithCaps({ edit: true });
  const { container } = renderPage();
  await screen.findByRole("button", { name: /Start revision/ });
  expect(await axe(container)).toHaveNoViolations();
});

describe("DocumentDetailPage URL-backed tabs", () => {
  test.each([
    ["overview", "Control metadata"],
    ["history", "Version history"],
    ["approvals", "Quality approval"],
    ["where-used", "Records produced under"],
    ["acks", "Acknowledgement coverage"],
  ] as const)("cold ?tab=%s renders the %s panel", async (tab, panelText) => {
    renderPage(`/documents/${ID}?tab=${tab}`);
    expect(await screen.findByText(panelText)).toBeInTheDocument();
  });

  test("an unknown or removed tab renders Overview without leaking raw URL text", async () => {
    const { container } = renderPage(`/documents/${ID}?tab=unknown-sentinel`);
    expect(await screen.findByText("Control metadata")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("unknown-sentinel");
  });

  test("live external tab changes and removal update the active panel", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="documents/:id" element={<DocumentTabNavigation />} />
      </Routes>,
      { route: `/documents/${ID}` },
    );
    expect(await screen.findByText("Control metadata")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "external history" }));
    expect(await screen.findByText("Version history")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "external approvals" }));
    expect(await screen.findByText("Quality approval")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "external where-used" }));
    expect(await screen.findByText("Records produced under")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "external acks" }));
    expect(await screen.findByText("Acknowledgement coverage")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "external unknown" }));
    expect(await screen.findByText("Control metadata")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "external overview" }));
    expect(await screen.findByText("Control metadata")).toBeInTheDocument();
  });

  test("tab controls replace history, delete the default tab, and leave route chrome neutral", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route path="documents/:id" element={<DocumentWithRouteChrome />} />
        </Routes>
        <DocumentHistoryControls />
        <LocationProbe />
      </RouteChromeProvider>,
      { route: `/documents/${ID}?sentinel=keep&checkpoint=baseline` },
    );
    await screen.findByText("Control metadata");
    await user.click(screen.getByRole("button", { name: "Prepare history" }));
    const historyTab = screen.getByRole("tab", { name: "History" });
    await user.click(historyTab);
    expect(await screen.findByText("Version history")).toBeInTheDocument();
    expect(screen.getByLabelText("Current location")).toHaveTextContent("tab=history");
    expect(document.title).toBe("EasySynQ — Document");
    expect(historyTab).toHaveFocus();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");

    await user.click(screen.getByRole("tab", { name: "Overview" }));
    expect(await screen.findByText("Control metadata")).toBeInTheDocument();
    expect(screen.getByLabelText("Current location")).not.toHaveTextContent("tab=");

    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
    );
    expect(screen.getByLabelText("Current location")).not.toHaveTextContent("tab=");
  });

  test("DocumentDetailPage has no a11y violations for the History tab", async () => {
    const { container } = renderPage(`/documents/${ID}?tab=history`);
    await screen.findByText("Version history");
    expect(await axe(container)).toHaveNoViolations();
  });
});

// S-web-8 review surfaces — the Next-review tile + the manage_metadata-gated edit modal.
// renderDetail is the same route helper as renderPage, aliased for readability.
const renderDetail = () => renderPage(`/documents/${ID}`);

describe("S-web-8 review surfaces", () => {
  test("renders the Next-review tile with days + badge", async () => {
    renderDetail();
    // "Next review" appears in the tile AND the ControlMetadata table row — both are correct.
    expect((await screen.findAllByText("Next review")).length).toBeGreaterThan(0);
    expect(screen.getByText(/\d+ days/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("Review state: Current").length).toBeGreaterThan(0);
  });

  test("no manage_metadata → no edit affordance", async () => {
    renderDetail();
    await screen.findAllByText("Next review");
    expect(screen.queryByRole("button", { name: "Edit review period" })).not.toBeInTheDocument();
  });

  test("manage_metadata → the modal opens, saves, and a REOPEN is pristine", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () =>
        HttpResponse.json({
          ...docFixture[0],
          capabilities: { ...detailCapabilities, manage_metadata: true },
        }),
      ),
    );
    renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: "Edit review period" }));
    // Dirty the field BEFORE cancelling — a persistently-mounted modal would keep "36" across the
    // reopen and this test would miss the S-web-7d trap entirely (a pristine field can't tell
    // remount from persistence).
    const input = await screen.findByLabelText("Review period (months)");
    await userEvent.clear(input);
    await userEvent.type(input, "36");
    expect(input).toHaveValue("36");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    // Reopen — conditional render means a fresh mount (the S-web-7d reopen trap)
    await userEvent.click(screen.getByRole("button", { name: "Edit review period" }));
    expect(await screen.findByLabelText("Review period (months)")).toHaveValue("24");
  });
});

// S-ack-2: the Acknowledged tile + the Acks tab.
describe("S-ack-2 acknowledgements", () => {
  test("renders the Acknowledged tile from the distribution coverage", async () => {
    renderPage();
    // the metric tile (persistent, above the tabs) shows the ratio.
    expect(await screen.findByText("Acknowledged")).toBeInTheDocument();
    expect(await screen.findByText("41 / 47")).toBeInTheDocument();
  });

  test("the Acks tab shows coverage; deep-link via ?tab=acks", async () => {
    renderPage(`/documents/${ID}?tab=acks`);
    // coverage ring is in the panel too (87% appears).
    expect(await screen.findByText("87%")).toBeInTheDocument();
  });

  test("clicking the Acks tab activates it", async () => {
    renderPage();
    await screen.findByText("Acknowledged"); // page loaded
    await userEvent.click(screen.getByRole("tab", { name: /acknowledgements/i }));
    expect(await screen.findByText(/Read-and-understood coverage/)).toBeInTheDocument();
  });

  test("release never shows an Effective document with stale not-yet-effective acknowledgement coverage", async () => {
    // This fails if release does not invalidate the distribution query, or if the tile renders
    // stale null coverage as "Not yet effective" while that invalidated query is refetching.
    let documentReads = 0;
    let distributionReads = 0;
    let releaseHeldDistribution: (() => void) | undefined;
    let markHeldDistributionRequested!: () => void;
    const heldDistributionRequested = new Promise<void>((resolve) => {
      markHeldDistributionRequested = resolve;
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const releaseFixture = docFixture[0]!;
    queryClient.setQueryData(["acknowledgements", ID], ackMatrixFixture);

    server.use(
      http.get("/api/v1/documents/:id", ({ params }) => {
        documentReads += 1;
        return HttpResponse.json({
          ...releaseFixture,
          id: String(params.id),
          current_state: documentReads === 1 ? "Approved" : "Effective",
          current_effective_version_id:
            documentReads === 1 ? null : releaseFixture.current_effective_version_id,
          effective_from: documentReads === 1 ? null : releaseFixture.effective_from,
          capabilities: { ...detailCapabilities, release: documentReads === 1 },
        });
      }),
      http.post("/api/v1/documents/:id/release", ({ params }) =>
        HttpResponse.json({ ...releaseFixture, id: String(params.id), current_state: "Effective" }),
      ),
      http.get("/api/v1/documents/:id/distribution", () => {
        distributionReads += 1;
        if (distributionReads === 1) return HttpResponse.json(distributionNoEffectiveFixture);
        markHeldDistributionRequested();
        return new Promise<HttpResponse<DistributionPayload>>((resolve) => {
          releaseHeldDistribution = () => resolve(HttpResponse.json(distributionFixture));
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="documents/:id" element={<DocumentDetailPage />} />
      </Routes>,
      { route: `/documents/${ID}`, queryClient },
    );

    await screen.findByLabelText("State: Approved");
    const acknowledgedTile = (await screen.findByText("Acknowledged")).parentElement;
    expect(acknowledgedTile).toBeInstanceOf(HTMLElement);
    if (!(acknowledgedTile instanceof HTMLElement)) {
      throw new Error("Acknowledged tile has no containing element");
    }
    expect(within(acknowledgedTile).getByText("Not yet effective")).toBeInTheDocument();

    let observedContradiction = false;
    const recordVisibleState = () => {
      if (
        document.body.querySelector('[aria-label="State: Effective"]') &&
        acknowledgedTile.textContent?.includes("Not yet effective")
      ) {
        observedContradiction = true;
      }
    };
    const transitionObserver = new MutationObserver(recordVisibleState);
    transitionObserver.observe(document.body, {
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
    });

    try {
      await user.click(screen.getByRole("tab", { name: /approvals/i }));
      await user.click(await screen.findByRole("button", { name: "Release" }));
      await user.click(await screen.findByRole("button", { name: "Release document" }));

      expect(await screen.findByLabelText("State: Effective")).toBeInTheDocument();
      expect(within(acknowledgedTile).queryByText("Not yet effective")).not.toBeInTheDocument();
      expect(
        within(acknowledgedTile).getByText("Refreshing acknowledgement coverage"),
      ).toBeInTheDocument();
      expect(observedContradiction).toBe(false);

      await heldDistributionRequested;

      expect(within(acknowledgedTile).queryByText("Not yet effective")).not.toBeInTheDocument();
      expect(
        within(acknowledgedTile).getByText("Refreshing acknowledgement coverage"),
      ).toBeInTheDocument();
      expect(observedContradiction).toBe(false);
      expect(queryClient.getQueryState(["acknowledgements", ID])?.isInvalidated).toBe(true);
      releaseHeldDistribution?.();
      expect(await screen.findByText("41 / 47")).toBeInTheDocument();
    } finally {
      transitionObserver.disconnect();
      releaseHeldDistribution?.();
    }
  });

  test("release shows unavailable acknowledgement coverage when its post-release refresh fails", async () => {
    // This fails if an Effective document maps a failed refresh's stale null coverage back to the
    // pre-release "Not yet effective" meaning.
    let documentReads = 0;
    let distributionReads = 0;
    const releaseFixture = docFixture[0]!;
    server.use(
      http.get("/api/v1/documents/:id", ({ params }) => {
        documentReads += 1;
        return HttpResponse.json({
          ...releaseFixture,
          id: String(params.id),
          current_state: documentReads === 1 ? "Approved" : "Effective",
          current_effective_version_id:
            documentReads === 1 ? null : releaseFixture.current_effective_version_id,
          effective_from: documentReads === 1 ? null : releaseFixture.effective_from,
          capabilities: { ...detailCapabilities, release: documentReads === 1 },
        });
      }),
      http.post("/api/v1/documents/:id/release", ({ params }) =>
        HttpResponse.json({ ...releaseFixture, id: String(params.id), current_state: "Effective" }),
      ),
      http.get("/api/v1/documents/:id/distribution", () => {
        distributionReads += 1;
        return distributionReads === 1
          ? HttpResponse.json(distributionNoEffectiveFixture)
          : HttpResponse.json({ code: "unavailable", title: "Unavailable" }, { status: 503 });
      }),
    );

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /approvals/i }));
    await user.click(await screen.findByRole("button", { name: "Release" }));
    await user.click(await screen.findByRole("button", { name: "Release document" }));

    await screen.findByLabelText("State: Effective");
    expect(await screen.findByText("Acknowledgement coverage unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Not yet effective")).not.toBeInTheDocument();
  });
});
