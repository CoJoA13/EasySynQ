import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RecordDownloadButton } from "./RecordDownloadButton";

const EVIDENCE_ENDPOINT =
  "/api/v1/records/re000001-0001-0001-0001-000000000001/evidence/abc123/download";
const RENDITION_ENDPOINT = "/api/v1/records/re000001-0001-0001-0001-000000000001/rendition";
const OBJECT_URL = "https://objects.example.test/records/evidence.pdf";
const NEXT_EVIDENCE_ENDPOINT =
  "/api/v1/records/re000002-0002-0002-0002-000000000002/evidence/def456/download";

afterEach(() => vi.restoreAllMocks());

test("keeps a maximum-length download label accessible inside a 44px constrained control", () => {
  const label = `Download ${"evidence-file-".repeat(20)}.pdf`;
  renderWithProviders(<RecordDownloadButton label={label} endpoint={EVIDENCE_ENDPOINT} />);

  const button = screen.getByRole("button", { name: label });
  expect(button).toHaveStyle({
    minHeight: "calc(2.75rem * var(--mantine-scale))",
    maxWidth: "100%",
  });
  const visibleLabel = screen.getByText(label);
  expect(visibleLabel.tagName).toBe("SPAN");
  expect(visibleLabel).toHaveStyle({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
});

function successfulDownload() {
  return HttpResponse.json({
    download_url: OBJECT_URL,
    sha256: "abc123",
    content_type: "application/pdf",
  });
}

test("each activation obtains a fresh presign and opens only the returned object-store URL", async () => {
  const user = userEvent.setup();
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  let presignCalls = 0;
  server.use(
    http.get(EVIDENCE_ENDPOINT, () => {
      presignCalls += 1;
      return successfulDownload();
    }),
  );
  renderWithProviders(
    <RecordDownloadButton label="Download evidence" endpoint={EVIDENCE_ENDPOINT} />,
  );

  const button = screen.getByRole("button", { name: "Download evidence" });
  await user.click(button);
  await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
  await user.click(button);
  await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(2));

  expect(presignCalls).toBe(2);
  expect(openSpy).toHaveBeenNthCalledWith(
    1,
    "https://objects.example.test/records/evidence.pdf",
    "_blank",
    "noopener,noreferrer",
  );
  expect(openSpy).toHaveBeenNthCalledWith(
    2,
    "https://objects.example.test/records/evidence.pdf",
    "_blank",
    "noopener,noreferrer",
  );
  expect(fetchSpy.mock.calls).toHaveLength(2);
  expect(fetchSpy.mock.calls.every(([url]) => String(url) === EVIDENCE_ENDPOINT)).toBe(true);
  expect(fetchSpy.mock.calls.some(([url]) => String(url) === OBJECT_URL)).toBe(false);
});

test("a failed evidence action leaves another action enabled and retry clears only its own error", async () => {
  const user = userEvent.setup();
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  let evidenceCalls = 0;
  server.use(
    http.get(EVIDENCE_ENDPOINT, () => {
      evidenceCalls += 1;
      return evidenceCalls === 1
        ? HttpResponse.json(
            { code: "storage_unavailable", title: "Storage unavailable" },
            { status: 503 },
          )
        : successfulDownload();
    }),
    http.get(RENDITION_ENDPOINT, () => successfulDownload()),
  );
  renderWithProviders(
    <>
      <RecordDownloadButton label="Download evidence" endpoint={EVIDENCE_ENDPOINT} />
      <RecordDownloadButton
        label="Download structured PDF"
        endpoint={RENDITION_ENDPOINT}
        pendingIsNormal
      />
    </>,
  );

  await user.click(screen.getByRole("button", { name: "Download evidence" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Couldn't prepare this download. Try again.",
  );
  expect(screen.getByRole("button", { name: "Download structured PDF" })).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "Download structured PDF" }));
  await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("alert")).toHaveTextContent("Couldn't prepare this download. Try again.");

  await user.click(screen.getByRole("button", { name: "Download evidence" }));
  await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(evidenceCalls).toBe(2);
});

test.each([
  [403, "permission_denied", "This download is unavailable to you."],
  [404, "not_found", "The requested evidence could not be found."],
  [503, "storage_unavailable", "Couldn't prepare this download. Try again."],
] as const)("renders the isolated %s presign failure", async (status, code, message) => {
  server.use(
    http.get(EVIDENCE_ENDPOINT, () =>
      HttpResponse.json({ code, title: "Presign failed" }, { status }),
    ),
  );
  renderWithProviders(
    <RecordDownloadButton label="Download evidence" endpoint={EVIDENCE_ENDPOINT} />,
  );

  await userEvent.setup().click(screen.getByRole("button", { name: "Download evidence" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
});

test("renders rendition_pending as a normal not-ready state without opening a URL", async () => {
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  server.use(
    http.get(RENDITION_ENDPOINT, () =>
      HttpResponse.json(
        { code: "rendition_pending", title: "Rendition not available" },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(
    <RecordDownloadButton
      label="Download structured PDF"
      endpoint={RENDITION_ENDPOINT}
      pendingIsNormal
    />,
  );

  await userEvent.setup().click(screen.getByRole("button", { name: "Download structured PDF" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Structured PDF is not ready yet");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(openSpy).not.toHaveBeenCalled();
});

function downloadResponse(downloadUrl: string, sha256: string): Response {
  return new Response(
    JSON.stringify({ download_url: downloadUrl, sha256, content_type: "application/pdf" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

test("an endpoint change aborts and ignores the old activation while the new activation owns state", async () => {
  const user = userEvent.setup();
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  let oldSignal: AbortSignal | undefined;
  let newSignal: AbortSignal | undefined;
  let finishOld: ((response: Response) => void) | undefined;
  let finishNew: ((response: Response) => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = String(input);
    return new Promise<Response>((resolve) => {
      if (path === EVIDENCE_ENDPOINT) {
        oldSignal = init?.signal ?? undefined;
        finishOld = resolve;
      } else if (path === NEXT_EVIDENCE_ENDPOINT) {
        newSignal = init?.signal ?? undefined;
        finishNew = resolve;
      } else {
        throw new Error(`Unexpected fetch ${path}`);
      }
    });
  });
  const rendered = renderWithProviders(
    <RecordDownloadButton label="Download old evidence" endpoint={EVIDENCE_ENDPOINT} />,
  );
  await user.click(screen.getByRole("button", { name: "Download old evidence" }));
  await waitFor(() => expect(finishOld).toBeTypeOf("function"));

  rendered.rerender(
    <RecordDownloadButton label="Download new evidence" endpoint={NEXT_EVIDENCE_ENDPOINT} />,
  );
  await waitFor(() => expect(oldSignal?.aborted).toBe(true));
  await user.click(screen.getByRole("button", { name: "Download new evidence" }));
  await waitFor(() => expect(finishNew).toBeTypeOf("function"));
  expect(newSignal?.aborted).toBe(false);

  await act(async () => {
    finishOld?.(downloadResponse("https://objects.example.test/records/old.pdf", "abc123"));
  });
  expect(openSpy).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Download new evidence" })).toBeDisabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  await act(async () => {
    finishNew?.(downloadResponse("https://objects.example.test/records/new.pdf", "def456"));
  });
  expect(openSpy).toHaveBeenCalledWith(
    "https://objects.example.test/records/new.pdf",
    "_blank",
    "noopener,noreferrer",
  );
  expect(screen.getByRole("button", { name: "Download new evidence" })).toBeEnabled();
});

test("unmount aborts an in-flight presign and prevents its completion from opening a URL", async () => {
  const user = userEvent.setup();
  const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
  let signal: AbortSignal | undefined;
  let finish: ((response: Response) => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
    signal = init?.signal ?? undefined;
    return new Promise<Response>((resolve) => {
      finish = resolve;
    });
  });
  const rendered = renderWithProviders(
    <RecordDownloadButton label="Download evidence" endpoint={EVIDENCE_ENDPOINT} />,
  );
  await user.click(screen.getByRole("button", { name: "Download evidence" }));
  await waitFor(() => expect(finish).toBeTypeOf("function"));

  rendered.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    finish?.(downloadResponse(OBJECT_URL, "abc123"));
  });
  expect(openSpy).not.toHaveBeenCalled();
});
