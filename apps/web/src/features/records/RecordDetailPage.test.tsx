import { axe } from "jest-axe";
import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { expect, test, vi } from "vitest";
import type { RecordDetail } from "../../lib/types";
import { Breadcrumb } from "../../app/shell/Breadcrumb";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RecordDetailPage } from "./RecordDetailPage";
import { expectSoundHeadingOutline } from "../../test/headingOutline";

const RECORD_ID = "re000001-0001-0001-0001-000000000001";
const NEXT_RECORD_ID = "re000002-0002-0002-0002-000000000002";

const recordDetailFixture: RecordDetail = {
  id: RECORD_ID,
  identifier: "REC-000041",
  kind: "RECORD",
  title: "Preventive-maintenance schedule",
  record_type: "EVIDENCE",
  classification: "Internal",
  framework_id: "fr000001-0001-0001-0001-000000000001",
  captured_at: "2026-06-01T09:00:00+00:00",
  captured_by: "us000001-0001-0001-0001-000000000001",
  captured_by_display_name: "Mara Quality",
  source_document_id: "11111111-1111-1111-1111-111111111111",
  source_document_identifier: "SOP-PUR-014",
  source_document_title: "Supplier Selection & Evaluation",
  source_document_readable: true,
  source_version_id: "dddd1111-1111-1111-1111-111111111111",
  source_version_label: "Rev B",
  retention_policy_id: "rp000001-0001-0001-0001-000000000001",
  retention_policy_name: "Quality records — 7 years",
  disposition_state: "ON_HOLD",
  legal_hold: true,
  has_structured_pdf: true,
  correction_of: "re000040-0040-0040-0040-000000000040",
  superseded_by_correction: "re000042-0042-0042-0042-000000000042",
  content_hash: "sha256:sealed-record-hash",
  content_hash_version: 2,
  form_field_values: {
    inspection_result: "Pass",
    measurements: [{ gauge_id: "G-7", in_tolerance: true }, null, 12.5],
    html_probe: "<img src=x onerror=alert(1)>",
  },
  retention_basis_date: "2026-06-01",
  correction_of_readable: true,
  superseded_by_correction_readable: false,
  created_at: "2026-06-01T09:01:00+00:00",
  evidence_blobs: [
    {
      sha256: "abc123",
      is_original: true,
      filename: "maintenance-schedule.pdf",
      content_type: "application/pdf",
      size_bytes: 1536,
      created_at: "2026-06-01T09:00:30+00:00",
    },
  ],
  evidence_links: [
    {
      id: "el000001-0001-0001-0001-000000000001",
      record_id: RECORD_ID,
      target_type: "document",
      target_id: "22222222-2222-2222-2222-222222222222",
      target_label: "WI-MNT-004 — Maintenance checks",
      target_readable: true,
      link_reason: "Governing work instruction",
      created_at: "2026-06-01T09:02:00+00:00",
    },
    {
      id: "el000002-0002-0002-0002-000000000002",
      record_id: RECORD_ID,
      target_type: "process",
      target_id: "pr000002-0002-0002-0002-000000000002",
      target_label: "Secret process label",
      target_readable: false,
      link_reason: null,
      created_at: null,
    },
    {
      id: "el000003-0003-0003-0003-000000000003",
      record_id: RECORD_ID,
      target_type: "clause",
      target_id: "cl000003-0003-0003-0003-000000000003",
      target_label: "ISO 9001:2015 7.5",
      target_readable: true,
      link_reason: "Documented information",
      created_at: "2026-06-01T09:03:00+00:00",
    },
    {
      id: "el000004-0004-0004-0004-000000000004",
      record_id: RECORD_ID,
      target_type: "document",
      target_id: "44444444-4444-4444-4444-444444444444",
      target_label: null,
      target_readable: true,
      link_reason: "Label withheld",
      created_at: null,
    },
  ],
};

function detailHandler(detail: RecordDetail = recordDetailFixture) {
  return http.get("/api/v1/records/:recordId", ({ params }) =>
    HttpResponse.json({ ...detail, id: String(params.recordId) }),
  );
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="Current location">{`${location.pathname}${location.search}`}</output>;
}

function ParameterChange() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(`/records/${NEXT_RECORD_ID}`)}>
      Open next record
    </button>
  );
}

function renderDetail(route = `/records/${RECORD_ID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="/records/:recordId" element={<><RecordDetailPage /><LocationProbe /><ParameterChange /></>} />
      <Route path="/records" element={<><h1>Records register</h1><LocationProbe /></>} />
      <Route path="/origin" element={<Link to={`/records/${RECORD_ID}`} state={{ from: "/records?q=REC-41&cursor=next" }}>Open detail</Link>} />
      <Route path="/bad-origin" element={<Link to={`/records/${RECORD_ID}`} state={{ from: "/documents/private" }}>Open detail</Link>} />
    </Routes>,
    { route },
  );
}

function renderDetailWithBreadcrumb(queryClient: QueryClient) {
  return renderWithProviders(
    <>
      <Breadcrumb />
      <Routes>
        <Route
          path="/records/:recordId"
          element={
            <>
              <RecordDetailPage />
              <ParameterChange />
            </>
          }
        />
      </Routes>
    </>,
    { route: `/records/${RECORD_ID}`, queryClient },
  );
}

test("renders the five detail groups, safe recursive values, and authorization-correct links", async () => {
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  server.use(detailHandler());
  const { container } = renderDetail();

  const title = await screen.findByRole("heading", { name: "Preventive-maintenance schedule" });
  expect(screen.getByText("REC-000041")).toBeInTheDocument();
  const state = screen.getByLabelText("Record state");
  expect(within(state).getByText("EVIDENCE")).toBeInTheDocument();
  expect(within(state).getByText("Internal")).toBeInTheDocument();
  expect(within(state).getByText("ON HOLD")).toBeInTheDocument();
  expect(within(state).getByText("Legal hold")).toBeInTheDocument();
  expect(title).toHaveFocus();

  const provenance = screen.getByRole("region", { name: "Provenance" });
  expect(provenance).toHaveTextContent("Mara Quality");
  expect(provenance).toHaveTextContent("fr000001-0001-0001-0001-000000000001");
  expect(provenance).toHaveTextContent("Seal version 2");
  expect(provenance).toHaveTextContent("sha256:sealed-record-hash");
  expect(within(provenance).getByRole("link", { name: /SOP-PUR-014/ })).toHaveAttribute(
    "href",
    "/documents/11111111-1111-1111-1111-111111111111",
  );

  const lifecycle = screen.getByRole("region", { name: "Lifecycle" });
  expect(lifecycle).toHaveTextContent("Quality records — 7 years");
  expect(lifecycle).toHaveTextContent("2026-06-01");
  expect(within(lifecycle).getByRole("link", { name: "Previous record" })).toHaveAttribute(
    "href",
    "/records/re000040-0040-0040-0040-000000000040",
  );
  expect(lifecycle).toHaveTextContent("Restricted related item");
  expect(lifecycle).not.toHaveTextContent("re000042-0042-0042-0042-000000000042");

  const evidence = screen.getByRole("region", { name: "Evidence files" });
  expect(evidence).toHaveTextContent("maintenance-schedule.pdf");
  expect(evidence).toHaveTextContent("application/pdf");
  expect(evidence).toHaveTextContent("1.5 KB");
  expect(evidence).toHaveTextContent("abc123");
  expect(evidence).toHaveTextContent("Original");
  expect(within(evidence).getByRole("button", { name: "Download maintenance-schedule.pdf" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Download structured PDF" })).toBeEnabled();
  expect(openSpy).not.toHaveBeenCalled();

  const structured = screen.getByRole("region", { name: "Structured values" });
  expect(structured).toHaveTextContent("Inspection result");
  expect(structured).toHaveTextContent("Measurements");
  expect(structured).toHaveTextContent("Gauge id");
  expect(structured).toHaveTextContent("In tolerance");
  expect(structured).toHaveTextContent("true");
  expect(structured).toHaveTextContent("null");
  expect(structured).toHaveTextContent("12.5");
  expect(structured).toHaveTextContent("<img src=x onerror=alert(1)>");
  expect(within(structured).queryByRole("img")).not.toBeInTheDocument();

  const links = screen.getByRole("region", { name: "Evidence for" });
  expect(within(links).getByRole("link", { name: "WI-MNT-004 — Maintenance checks" })).toHaveAttribute(
    "href",
    "/documents/22222222-2222-2222-2222-222222222222",
  );
  expect(links).toHaveTextContent("Restricted related item");
  expect(links).not.toHaveTextContent("Secret process label");
  expect(links).toHaveTextContent("ISO 9001:2015 7.5");
  expect(within(links).queryByRole("link", { name: "ISO 9001:2015 7.5" })).not.toBeInTheDocument();
  expect(links.querySelector('a[href="/documents/44444444-4444-4444-4444-444444444444"]')).toBeNull();
  expect(within(links).getAllByText("Restricted related item")).toHaveLength(2);
  expect(await axe(container)).toHaveNoViolations();
  expectSoundHeadingOutline();
});

test("omits empty optional sections and hides unreadable source and correction targets", async () => {
  const minimal: RecordDetail = {
    ...recordDetailFixture,
    source_document_readable: false,
    source_document_identifier: "Hidden source label",
    source_document_title: "Hidden source title",
    correction_of_readable: false,
    superseded_by_correction: null,
    superseded_by_correction_readable: false,
    form_field_values: null,
    has_structured_pdf: false,
    evidence_blobs: [],
    evidence_links: [],
  };
  server.use(detailHandler(minimal));
  renderDetail();

  await screen.findByRole("heading", { name: "Preventive-maintenance schedule" });
  expect(screen.queryByRole("region", { name: "Evidence files" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Structured values" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Evidence for" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Download structured PDF" })).not.toBeInTheDocument();
  expect(screen.queryByText("Hidden source label")).not.toBeInTheDocument();
  expect(screen.queryByText("Hidden source title")).not.toBeInTheDocument();
  expect(screen.queryByText("re000040-0040-0040-0040-000000000040")).not.toBeInTheDocument();
  expect(screen.getAllByText("Restricted related item").length).toBeGreaterThanOrEqual(2);
});

test("Back accepts a records origin and restores focus on the detail heading", async () => {
  const user = userEvent.setup();
  server.use(detailHandler());
  renderDetail("/origin");
  await user.click(screen.getByRole("link", { name: "Open detail" }));
  expect(await screen.findByRole("heading", { name: "Preventive-maintenance schedule" })).toHaveFocus();
  expect(await screen.findByRole("link", { name: "Back to records" })).toHaveAttribute(
    "href",
    "/records?q=REC-41&cursor=next",
  );

  const back = screen.getByRole("link", { name: "Back to records" });
  await user.click(back);
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "/records?q=REC-41&cursor=next",
  );
});

test("Back rejects a non-records origin", async () => {
  const user = userEvent.setup();
  server.use(detailHandler());
  renderDetail("/bad-origin");
  await user.click(screen.getByRole("link", { name: "Open detail" }));
  expect(await screen.findByRole("link", { name: "Back to records" })).toHaveAttribute(
    "href",
    "/records",
  );
});

test("Back falls back to /records on a bookmarked detail route", async () => {
  server.use(detailHandler());
  renderDetail();
  expect(await screen.findByRole("link", { name: "Back to records" })).toHaveAttribute(
    "href",
    "/records",
  );
});

test.each([
  [403, "Record access is unavailable."],
  [404, "This record could not be found."],
] as const)("renders a detail %s state without cached record content", async (status, copy) => {
  server.use(
    http.get(`/api/v1/records/${RECORD_ID}`, () =>
      HttpResponse.json({ code: status === 403 ? "permission_denied" : "not_found", title: copy }, { status }),
    ),
  );
  renderDetail();

  expect(await screen.findByText(copy)).toBeInTheDocument();
  expect(screen.queryByText("REC-000041")).not.toBeInTheDocument();
  expect(screen.queryByText("Preventive-maintenance schedule")).not.toBeInTheDocument();
});

test("retries a generic detail failure", async () => {
  let calls = 0;
  server.use(
    http.get(`/api/v1/records/${RECORD_ID}`, () => {
      calls += 1;
      return calls === 1
        ? HttpResponse.json({ code: "storage_unavailable", title: "Temporary failure" }, { status: 503 })
        : HttpResponse.json(recordDetailFixture);
    }),
  );
  renderDetail();

  await userEvent.setup().click(await screen.findByRole("button", { name: "Try again" }));
  expect(await screen.findByText("REC-000041")).toBeInTheDocument();
  expect(calls).toBe(2);
});

test.each([403, 404] as const)(
  "clears the previous record when a parameter change returns %s",
  async (status) => {
    server.use(
      http.get("/api/v1/records/:recordId", ({ params }) => {
        if (String(params.recordId) === NEXT_RECORD_ID) {
          return HttpResponse.json(
            { code: status === 403 ? "permission_denied" : "not_found", title: "Unavailable" },
            { status },
          );
        }
        return HttpResponse.json(recordDetailFixture);
      }),
    );
    renderDetail();
    await screen.findByText("REC-000041");

    await userEvent.setup().click(screen.getByRole("button", { name: "Open next record" }));
    await waitFor(() => {
      expect(screen.queryByText("REC-000041")).not.toBeInTheDocument();
      expect(screen.queryByText("Preventive-maintenance schedule")).not.toBeInTheDocument();
    });
    expect(
      await screen.findByText(status === 403 ? "Record access is unavailable." : "This record could not be found."),
    ).toBeInTheDocument();
  },
);

test.each([403, 404] as const)(
  "keeps a cached destination out of the page and breadcrumb before and after %s",
  async (status) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const cachedDestination: RecordDetail = {
      ...recordDetailFixture,
      id: NEXT_RECORD_ID,
      identifier: "REC-CACHED-B",
      title: "Cached destination record",
    };
    queryClient.setQueryData(["record", NEXT_RECORD_ID], cachedDestination);
    let finishDestination: ((response: Response) => void) | undefined;
    server.use(
      http.get("/api/v1/records/:recordId", ({ params }) => {
        if (String(params.recordId) === NEXT_RECORD_ID) {
          return new Promise<Response>((resolve) => {
            finishDestination = resolve;
          });
        }
        return HttpResponse.json(recordDetailFixture);
      }),
    );
    renderDetailWithBreadcrumb(queryClient);
    await screen.findByRole("heading", { name: "Preventive-maintenance schedule" });

    await userEvent.setup().click(screen.getByRole("button", { name: "Open next record" }));
    await waitFor(() => expect(finishDestination).toBeTypeOf("function"));
    expect(screen.queryAllByText("REC-CACHED-B")).toHaveLength(0);
    expect(screen.queryByText("Cached destination record")).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("Breadcrumb")).queryByText("REC-CACHED-B")).not.toBeInTheDocument();

    await act(async () => {
      finishDestination?.(
        HttpResponse.json(
          { code: status === 403 ? "permission_denied" : "not_found", title: "Unavailable" },
          { status },
        ),
      );
    });
    expect(
      await screen.findByText(
        status === 403 ? "Record access is unavailable." : "This record could not be found.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryAllByText("REC-CACHED-B")).toHaveLength(0);
    expect(screen.queryByText("Cached destination record")).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("Breadcrumb")).queryByText("REC-CACHED-B")).not.toBeInTheDocument();
  },
);
