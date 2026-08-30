import { screen, waitFor } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "../../app/shell/AppShell";
import { RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ReportsRegisterPage } from "./ReportsRegisterPage";
import type { DocumentControlRegister } from "../../lib/types";

const REG: DocumentControlRegister = {
  provenance: {
    report_name: "Master Document List",
    generated_by: "Mara",
    generated_at: "2026-07-19T12:00:00+00:00",
    as_of: "2026-07-19T12:00:00+00:00",
    scope: "org:DEFAULT",
    app_version: "0.1.0",
    filters: {},
    row_count: 2,
    content_hash: "sha256:abc123",
    process_scope: null,
    excluded_processes: null,
  },
  rows: [
    {
      id: "1",
      identifier: "SOP-QA-001",
      title: "Document Control",
      document_type_id: null,
      document_type: "SOP",
      current_state: "Effective",
      owner_user_id: "u1",
      owner_display: "Priya",
      effective_revision_label: "Rev A",
      effective_from: "2026-06-01T00:00:00+00:00",
      blob_sha256: "deadbeefcafef00d",
      clause_refs: [{ clause: "7.5.3", starred: true }],
      // pr000001-… is "Purchasing" in the base processesFixture (test/msw/handlers.ts) — resolves to
      // a friendly name via useProcesses(), the same hook Risk/CAPA/Objectives already reuse.
      process_links: ["pr000001-0001-0001-0001-000000000001"],
      approved_by: "Ken",
      approved_on: "2026-06-01T00:00:00+00:00",
      next_review_due: "2026-06-01",
      review_state: "overdue",
    },
    {
      id: "2",
      identifier: "WI-QA-002",
      title: "Aardvark Work Instruction",
      document_type_id: null,
      document_type: "WI",
      current_state: "Draft",
      owner_user_id: "u2",
      owner_display: "Diego",
      effective_revision_label: null,
      effective_from: null,
      blob_sha256: null,
      clause_refs: [],
      process_links: [],
      approved_by: null,
      approved_on: null,
      next_review_due: null,
      review_state: null,
    },
  ],
} satisfies DocumentControlRegister;

function QueryProbe() {
  return <output aria-label="Current query">{useLocation().search}</output>;
}

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/?sentinel=keep&checkpoint=prepared")}>
        Prepare history
      </button>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}

function ChromeOutlet() {
  useRouteChrome();
  return <Outlet />;
}

describe("ReportsRegisterPage", () => {
  it("replaces report facet and clear history while preserving unrelated query state", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <HistoryControls />
        <QueryProbe />
      </>,
      { route: "/?sentinel=keep" },
    );
    await screen.findByText("SOP-QA-001");
    await user.click(screen.getByRole("button", { name: "Prepare history" }));
    await user.click(screen.getByRole("textbox", { name: "Status" }));
    await user.click(await screen.findByRole("option", { name: "Effective" }));
    expect(screen.getByLabelText("Current query")).toHaveTextContent("sentinel=keep");
    expect(screen.getByLabelText("Current query")).toHaveTextContent("checkpoint=prepared");
    expect(screen.getByLabelText("Current query")).toHaveTextContent("state=Effective");
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).toHaveTextContent("?sentinel=keep"),
    );

    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).toHaveTextContent("?sentinel=keep"),
    );
    expect(screen.getByLabelText("Current query")).not.toHaveTextContent("checkpoint=prepared");
    expect(screen.getByLabelText("Current query")).not.toHaveTextContent("state=Effective");
  });

  it("replaces every report filter and clear action without dropping unrelated state", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <HistoryControls />
        <QueryProbe />
      </>,
      { route: "/?sentinel=keep&checkpoint=baseline" },
    );
    await screen.findByText("SOP-QA-001");

    const cases = [
      ["Status", "Effective", "state=Effective"],
      ["Type", "Procedure", "type=aaaa1111-1111-1111-1111-111111111111"],
      ["Owner", "Mara Quality", "owner=bbbb1111-1111-1111-1111-111111111111"],
      ["Clause", "7.5.3", "clause=7.5.3"],
      ["Effective date", "Last 30 days", "eff=30d"],
      ["Process", "Purchasing", "process=pr000001-0001-0001-0001-000000000001"],
    ] as const;

    for (const [label, option, expected] of cases) {
      await user.click(screen.getByRole("button", { name: "Prepare history" }));
      await user.click(screen.getByRole("textbox", { name: label }));
      await user.click(await screen.findByRole("option", { name: option }));
      await waitFor(() =>
        expect(screen.getByLabelText("Current query")).toHaveTextContent(expected),
      );
      expect(screen.getByLabelText("Current query")).toHaveTextContent("sentinel=keep");
      expect(screen.getByLabelText("Current query")).toHaveTextContent("checkpoint=prepared");
      await user.click(screen.getByRole("button", { name: "Back" }));
      await waitFor(() =>
        expect(screen.getByLabelText("Current query")).toHaveTextContent("checkpoint=baseline"),
      );
    }

    await user.click(screen.getByRole("button", { name: "Prepare history" }));
    await user.click(screen.getByRole("textbox", { name: "Status" }));
    await user.click(await screen.findByRole("option", { name: "Effective" }));
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).toHaveTextContent(
        "?sentinel=keep&checkpoint=prepared",
      ),
    );
    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).toHaveTextContent("checkpoint=baseline"),
    );
  });

  it("a report ordinary filter edit leaves route chrome and focus untouched", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route element={<ChromeOutlet />}>
            <Route element={<AppShell />}>
              <Route path="reports/document-control" element={<ReportsRegisterPage />} />
            </Route>
          </Route>
        </Routes>
      </RouteChromeProvider>,
      { route: "/reports/document-control" },
    );
    await screen.findByText("SOP-QA-001");
    const sort = screen.getByRole("button", { name: "Sort by Identifier" });
    await user.click(sort);

    expect(document.title).toBe("EasySynQ — Master document list");
    expect(sort).toHaveFocus();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  });

  it("renders the provenance banner + a register row", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();
    // These two are deliberately DIFFERENT strings, so pin each rather than counting one twice.
    // The heading is a sentence-case UI label; the provenance banner carries the artefact's formal
    // Title Case name, which comes from the API's `report_name` and is what an auditor is handed.
    // getByText is case-sensitive, so neither query can satisfy itself with the other's string.
    expect(screen.getByRole("heading", { name: "Master document list" })).toBeInTheDocument();
    expect(screen.getByText("Master Document List")).toBeInTheDocument();
    expect(screen.getByText(/sha256:abc123/)).toBeInTheDocument();
    expect(screen.getByText("Rev A")).toBeInTheDocument();
    expect(
      screen.getByText("Clause 7.5.3, mandatory").closest("[data-clause-badge]"),
    ).toHaveAttribute("data-clause-badge");
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
    // RAG signal: the review-state badge renders next to the next-review date (never colour alone).
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });

  it("surfaces the audit columns: effective_from, approved_by, approved_on, process_links, blob_sha256 (FIX 5)", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    // effective_from, approved_on, AND next_review_due all render this date (three distinct cells —
    // the fixture's row1 happens to share one date across all three).
    expect(screen.getAllByText("2026-06-01")).toHaveLength(3);
    expect(screen.getByText("Ken")).toBeInTheDocument(); // approved_by
    // The blob sha256 renders truncated (never the raw dangerous-length string as a link/HTML), the
    // full value lives in the native title tooltip — a plain text node either way.
    const sha = await screen.findByTitle("deadbeefcafef00d");
    expect(sha).toHaveTextContent("deadbeefcafe…");
    // process_links resolves via the shared useProcesses() directory to a friendly name in the title.
    const badge = await screen.findByTitle("Purchasing");
    expect(badge).toHaveTextContent("1");
    // The second row's nulls render the calm dash, never blank cells.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows a calm no-access panel on 403", async () => {
    server.use(
      http.get("/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });

  it("shows a calm error (not an infinite loader) on a non-403 failure", async () => {
    server.use(
      http.get("/api/v1/reports/document-control", () =>
        HttpResponse.json({ title: "boom" }, { status: 500 }),
      ),
    );
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText(/Couldn't load the register/)).toBeInTheDocument();
  });

  it("reuses the baseline response when no filters are active", async () => {
    let requests = 0;
    server.use(
      http.get("/api/v1/reports/document-control", () => {
        requests += 1;
        return HttpResponse.json(REG);
      }),
    );
    renderWithProviders(<ReportsRegisterPage />);
    expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();

    // Let the success render and its effects settle. Enabling a second staleTime:0 observer after
    // the baseline completes would have reached the handler again by now.
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(requests).toBe(1);
  });

  it("keeps an independent filtered report usable and retries its failed facet baseline", async () => {
    const user = userEvent.setup();
    let releaseBaseline: () => void = () => undefined;
    const baselineGate = new Promise<void>((resolve) => {
      releaseBaseline = resolve;
    });
    let baselineStarted = false;
    let baselineFinished = false;
    let baselineRequests = 0;
    let filteredRequests = 0;
    server.use(
      http.get("/api/v1/reports/document-control", async ({ request }) => {
        const query = new URL(request.url).searchParams;
        if (query.get("filter[current_state][eq]") === "Effective") {
          filteredRequests += 1;
          return HttpResponse.json(REG);
        }
        baselineRequests += 1;
        if (baselineRequests > 1) return HttpResponse.json(REG);
        baselineStarted = true;
        await baselineGate;
        baselineFinished = true;
        return HttpResponse.json({ title: "baseline failed" }, { status: 500 });
      }),
    );

    renderWithProviders(<ReportsRegisterPage />, { route: "/?state=Effective" });
    try {
      await waitFor(() => expect(baselineStarted).toBe(true));
      expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();
      expect(filteredRequests).toBe(1);
    } finally {
      releaseBaseline();
    }

    await waitFor(() => expect(baselineFinished).toBe(true));
    expect(screen.getByText("SOP-QA-001")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load the register/)).not.toBeInTheDocument();
    expect(
      await screen.findByText("Couldn't load Process and Clause filter options."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(baselineRequests).toBe(2));
    expect(
      screen.queryByText("Couldn't load Process and Clause filter options."),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Process" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Clause" })).toBeInTheDocument();
  });

  it("retries failed baseline and filtered requests together", async () => {
    const user = userEvent.setup();
    let baselineRequests = 0;
    let filteredRequests = 0;
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        const filtered =
          new URL(request.url).searchParams.get("filter[current_state][eq]") === "Effective";
        if (filtered) {
          filteredRequests += 1;
          return filteredRequests === 1
            ? HttpResponse.json({ title: "filtered failed" }, { status: 500 })
            : HttpResponse.json(REG);
        }
        baselineRequests += 1;
        return baselineRequests === 1
          ? HttpResponse.json({ title: "baseline failed" }, { status: 500 })
          : HttpResponse.json(REG);
      }),
    );

    renderWithProviders(<ReportsRegisterPage />, { route: "/?state=Effective" });
    expect(await screen.findByText(/Couldn't load the register/)).toBeInTheDocument();
    await waitFor(() => {
      expect(baselineRequests).toBe(1);
      expect(filteredRequests).toBe(1);
    });

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("SOP-QA-001")).toBeInTheDocument();
    expect(baselineRequests).toBe(2);
    expect(filteredRequests).toBe(2);
  });

  it("debounced search filters rows by identifier / title / type", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search"), "aardvark");
    await waitFor(() => expect(screen.queryByText("SOP-QA-001")).not.toBeInTheDocument());
    expect(screen.getByText("WI-QA-002")).toBeInTheDocument();
  });

  it("sorts by the Identifier column", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    // Default sort is identifier asc: SOP-QA-001 before WI-QA-002.
    let cells = screen.getAllByText(/^(SOP-QA-001|WI-QA-002)$/);
    expect(cells.map((c) => c.textContent)).toEqual(["SOP-QA-001", "WI-QA-002"]);
    await user.click(screen.getByRole("button", { name: "Sort by Identifier" }));
    cells = screen.getAllByText(/^(SOP-QA-001|WI-QA-002)$/);
    expect(cells.map((c) => c.textContent)).toEqual(["WI-QA-002", "SOP-QA-001"]);
  });

  it("has no axe violations", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    const { container } = renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(await axe(container)).toHaveNoViolations();
  });

  // FIX 4, mutation-distinguishing: if useDocumentControlRegister ignored the facet (e.g. never
  // threaded `filters` into buildFilterParams / the request URL), the captured URL after the change
  // would still lack `filter[process_id][eq]` and this assertion would fail.
  it("wires a facet change (Process) to the API as filter[process_id][eq]", async () => {
    const user = userEvent.setup();
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
    );
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(seenUrls[0]).not.toContain("filter%5Bprocess_id%5D");

    // getByLabelText would also match the (portaled, hidden-until-opened) listbox — which shares the
    // same aria-labelledby as the input — so target the input by its textbox role instead.
    await user.click(screen.getByRole("textbox", { name: "Process" }));
    await user.click(await screen.findByRole("option", { name: "Purchasing" }));

    await waitFor(() =>
      expect(seenUrls.at(-1)).toContain(
        "filter%5Bprocess_id%5D%5Beq%5D=pr000001-0001-0001-0001-000000000001",
      ),
    );
  });

  // #334: option membership comes from the permission-filtered report, not the broader process
  // catalog. Even though the default MSW catalog has Purchasing, no visible row links a process.
  it("hides the Process facet when no caller-visible report row links a process", async () => {
    const reg: DocumentControlRegister = {
      ...REG,
      rows: REG.rows.map((row) => ({ ...row, process_links: [] })),
    };
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(reg)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.queryByRole("textbox", { name: "Process" })).not.toBeInTheDocument();
  });

  // Acceptance proof: the seeded Process Owner has PROCESS-scoped report.read but no process.read
  // or clauseMap.read. The report rows + process_scope provenance still make both facets usable;
  // the denied catalogs are label enhancements only, never option authorities.
  it("populates Process and Clause facets from report data for a delegated reader", async () => {
    const user = userEvent.setup();
    const seenUrls: string[] = [];
    const scopedReg: DocumentControlRegister = {
      ...REG,
      provenance: {
        ...REG.provenance,
        process_scope: [{ id: "pr000001-0001-0001-0001-000000000001", name: "Purchasing" }],
      },
    };
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(scopedReg);
      }),
      http.get("/api/v1/processes", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
      http.get("/api/v1/clauses", () => HttpResponse.json({ title: "Forbidden" }, { status: 403 })),
    );
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");

    await user.click(screen.getByRole("textbox", { name: "Process" }));
    await user.click(await screen.findByRole("option", { name: "Purchasing" }));
    await waitFor(() =>
      expect(seenUrls.at(-1)).toContain(
        "filter%5Bprocess_id%5D%5Beq%5D=pr000001-0001-0001-0001-000000000001",
      ),
    );

    await user.click(screen.getByRole("textbox", { name: "Clause" }));
    await user.click(await screen.findByRole("option", { name: "7.5.3" }));
    await waitFor(() => {
      const latest = seenUrls.at(-1) ?? "";
      expect(latest).toContain(
        "filter%5Bprocess_id%5D%5Beq%5D=pr000001-0001-0001-0001-000000000001",
      );
      expect(latest).toContain("filter%5Bclause_refs%5D%5Bhas%5D=7.5.3");
    });
  });

  // FIX 2 (Codex round 5, P2): the provenance banner must render `generated_at` in the ORG
  // timezone/offset carried by the string itself, never browser-tz-converted. +14:00 is chosen so
  // no real browser timezone coincides with it — a `new Date(...).toLocaleString()` render would
  // shift the calendar date/time away from the org-local wall clock the string encodes.
  // Mutation-distinguishing: fails if the banner routes through browser-tz Date conversion.
  it("renders the provenance generated_at in the organization timezone, not the browser's (FIX 2)", async () => {
    const reg: DocumentControlRegister = {
      ...REG,
      provenance: { ...REG.provenance, generated_at: "2026-06-20T00:00:00+14:00" },
    };
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(reg)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.getByText(/2026-06-20 00:00 \(UTC\+14:00\)/)).toBeInTheDocument();
    // Never the browser-shifted calendar date a naive `Date` conversion would produce.
    expect(screen.queryByText(/2026-06-19/)).not.toBeInTheDocument();
  });

  // #334: a catalog entry is not enough to make a URL facet representable; only values present in
  // the permission-filtered baseline may narrow the report. This unknown id is never sent.
  it("does not apply a stale ?process= URL filter absent from report data", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
    );
    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <QueryProbe />
      </>,
      { route: "/?process=pr999999-9999-9999-9999-999999999999" },
    );
    await screen.findByText("SOP-QA-001");
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).not.toHaveTextContent("process="),
    );
    expect(seenUrls.every((url) => !url.includes("filter%5Bprocess_id%5D"))).toBe(true);
  });

  // The flip side: a process id present in a visible baseline row is applied even when the separate
  // process catalog is forbidden.
  it("applies a ?process= URL filter when report data represents it", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
      http.get("/api/v1/processes", () =>
        HttpResponse.json({ title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<ReportsRegisterPage />, {
      route: "/?process=pr000001-0001-0001-0001-000000000001",
    });
    await screen.findByText("SOP-QA-001");
    await waitFor(() =>
      expect(seenUrls.at(-1)).toContain(
        "filter%5Bprocess_id%5D%5Beq%5D=pr000001-0001-0001-0001-000000000001",
      ),
    );
  });

  it("waits for a fresh baseline before validating cached Process and Clause membership", async () => {
    const staleReg: DocumentControlRegister = {
      ...REG,
      rows: REG.rows.map((row) => ({ ...row, clause_refs: [], process_links: [] })),
    };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["document-control-register", {}], staleReg);

    let releaseBaseline: () => void = () => undefined;
    const baselineGate = new Promise<void>((resolve) => {
      releaseBaseline = resolve;
    });
    let baselineStarted = false;
    const filteredUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", async ({ request }) => {
        const query = new URL(request.url).searchParams;
        if (query.has("filter[process_id][eq]") || query.has("filter[clause_refs][has]")) {
          filteredUrls.push(request.url);
          return HttpResponse.json(REG);
        }
        baselineStarted = true;
        await baselineGate;
        return HttpResponse.json(REG);
      }),
    );

    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <QueryProbe />
      </>,
      {
        route: "/?process=pr000001-0001-0001-0001-000000000001&clause=7.5.3",
        queryClient,
      },
    );
    try {
      await waitFor(() => expect(baselineStarted).toBe(true));
      // Give the cached-data render its effects a turn: neither bookmark may be removed or sent
      // until the fresh response supersedes this older snapshot.
      await new Promise((resolve) => setTimeout(resolve, 25));
      expect(screen.getByLabelText("Current query")).toHaveTextContent(
        "process=pr000001-0001-0001-0001-000000000001",
      );
      expect(screen.getByLabelText("Current query")).toHaveTextContent("clause=7.5.3");
      expect(filteredUrls).toHaveLength(0);
    } finally {
      releaseBaseline();
    }

    await waitFor(() => expect(filteredUrls).toHaveLength(1));
    expect(filteredUrls[0]).toContain(
      "filter%5Bprocess_id%5D%5Beq%5D=pr000001-0001-0001-0001-000000000001",
    );
    expect(filteredUrls[0]).toContain("filter%5Bclause_refs%5D%5Bhas%5D=7.5.3");
    expect(screen.getByLabelText("Current query")).toHaveTextContent(
      "process=pr000001-0001-0001-0001-000000000001",
    );
    expect(screen.getByLabelText("Current query")).toHaveTextContent("clause=7.5.3");
  });

  // The former process-only URL guard is deliberately generalized to clause: 8.4 exists in the
  // catalog fixture, but not in any caller-visible REG row, so it must never be applied invisibly.
  it("does not apply a stale ?clause= URL filter absent from report data", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
    );
    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <QueryProbe />
      </>,
      { route: "/?clause=8.4" },
    );
    await screen.findByText("SOP-QA-001");
    await waitFor(() =>
      expect(screen.getByLabelText("Current query")).not.toHaveTextContent("clause="),
    );
    expect(seenUrls.every((url) => !url.includes("filter%5Bclause_refs%5D"))).toBe(true);
  });

  it("applies a ?clause= URL filter when report data represents it", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
      http.get("/api/v1/clauses", () => HttpResponse.json({ title: "Forbidden" }, { status: 403 })),
    );
    renderWithProviders(<ReportsRegisterPage />, { route: "/?clause=7.5.3" });
    await screen.findByText("SOP-QA-001");
    await waitFor(() =>
      expect(seenUrls.at(-1)).toContain("filter%5Bclause_refs%5D%5Bhas%5D=7.5.3"),
    );
  });

  // S-clause-rollup: the server filter matches descendants, so a PARENT clause deep link is now a
  // meaningful, OpenAPI-documented filter — the representability guard must accept it when any
  // visible row maps to a descendant (7.5 over a 7.5.3-mapped row), not strip it as stale.
  it("applies a parent ?clause= URL filter whose subtree the report data represents", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get("/api/v1/reports/document-control", ({ request }) => {
        seenUrls.push(request.url);
        return HttpResponse.json(REG);
      }),
      http.get("/api/v1/clauses", () => HttpResponse.json({ title: "Forbidden" }, { status: 403 })),
    );
    renderWithProviders(
      <>
        <ReportsRegisterPage />
        <QueryProbe />
      </>,
      { route: "/?clause=7.5" },
    );
    await screen.findByText("SOP-QA-001");
    await waitFor(() => expect(seenUrls.at(-1)).toContain("filter%5Bclause_refs%5D%5Bhas%5D=7.5"));
    expect(screen.getByLabelText("Current query")).toHaveTextContent("clause=7.5");
    // The controlled Select must DISPLAY the accepted parent (Codex P2: options built only from
    // exact row refs rendered it blank over a filtered register — an invisible active filter).
    // Ancestors are now facet options, which also makes the parent re-selectable after clearing.
    expect(screen.getByRole("textbox", { name: "Clause" })).toHaveValue("7.5");
  });

  // Codex round 6 FIX 2: `scope` alone (always `org:<short_code>`) can't distinguish an org-wide
  // register from a PROCESS-scoped one — the banner must call it out explicitly when
  // `process_scope` is populated.
  it("shows the process-limited scope line when provenance.process_scope is populated", async () => {
    const reg: DocumentControlRegister = {
      ...REG,
      provenance: {
        ...REG.provenance,
        process_scope: [
          { id: "pr000001-0001-0001-0001-000000000001", name: "Purchasing" },
          { id: "pr000002-0002-0002-0002-000000000002", name: "Design" },
        ],
      },
    };
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(reg)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.getByText("Scope limited to processes: Purchasing, Design")).toBeInTheDocument();
  });

  // The flip side: an org-wide reader (process_scope: null, REG's default) sees no such line.
  it("does not show the process-limited scope line when provenance.process_scope is null", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.queryByText(/Scope limited to processes/)).not.toBeInTheDocument();
  });

  // #335 fix 1: a SYSTEM report.read ALLOW + PROCESS DENY keeps process_scope null (org-wide) but
  // records the denied process in excluded_processes — the banner must surface it so a restricted
  // register can't be mistaken for the org-wide one.
  it("shows the excluded-processes line when provenance.excluded_processes is populated", async () => {
    const reg: DocumentControlRegister = {
      ...REG,
      provenance: {
        ...REG.provenance,
        process_scope: null, // org-wide by the SYSTEM ALLOW
        excluded_processes: [{ id: "pr000003-0003-0003-0003-000000000003", name: "Logistics" }],
      },
    };
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(reg)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.getByText("Excludes processes: Logistics")).toBeInTheDocument();
  });

  // The flip side: no exclusions (REG's default) → no excludes line.
  it("does not show the excluded-processes line when provenance.excluded_processes is null", async () => {
    server.use(http.get("/api/v1/reports/document-control", () => HttpResponse.json(REG)));
    renderWithProviders(<ReportsRegisterPage />);
    await screen.findByText("SOP-QA-001");
    expect(screen.queryByText(/Excludes processes/)).not.toBeInTheDocument();
  });
});
