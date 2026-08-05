import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
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

const ID = "11111111-1111-1111-1111-111111111111";

function renderPage(route = `/documents/${ID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="documents/:id" element={<DocumentDetailPage />} />
    </Routes>,
    { route },
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
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
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

    await user.click(await screen.findByRole("tab", { name: /approvals/i }));
    await user.click(await screen.findByRole("button", { name: "Release" }));
    await user.click(await screen.findByRole("button", { name: "Release document" }));
    await screen.findByLabelText("State: Effective");

    try {
      expect(screen.queryByText("Not yet effective")).not.toBeInTheDocument();
      await heldDistributionRequested;
      expect(queryClient.getQueryState(["acknowledgements", ID])?.isInvalidated).toBe(true);
      releaseHeldDistribution?.();
      expect(await screen.findByText("41 / 47")).toBeInTheDocument();
    } finally {
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
