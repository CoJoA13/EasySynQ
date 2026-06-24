// apps/web/src/features/notifications/stream.test.ts
import { describe, expect, it, vi } from "vitest";
import { openNotificationStream, parseSseFrame, sleepWithSignal } from "./stream";

function streamResponse(chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("parseSseFrame", () => {
  it("reads the event name (LF)", () => {
    expect(parseSseFrame("event: notify\ndata: ")).toEqual({ event: "notify", data: "" });
  });
  it("ignores comment/heartbeat lines", () => {
    expect(parseSseFrame(": ping").event).toBe("message");
  });
  it("joins multiple data lines", () => {
    expect(parseSseFrame("event: x\ndata: a\ndata: b")).toEqual({ event: "x", data: "a\nb" });
  });
  it("tolerates CRLF lines", () => {
    expect(parseSseFrame("event: notify\r\ndata: ")).toEqual({ event: "notify", data: "" });
  });
});

describe("openNotificationStream", () => {
  it("calls onNudge once per notify frame", async () => {
    const onNudge = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse(["event: notify\ndata: \n\n", "event: notify\ndata: \n\n"]),
    );
    await openNotificationStream("t", onNudge, new AbortController().signal);
    expect(onNudge).toHaveBeenCalledTimes(2);
    vi.restoreAllMocks();
  });

  it("ignores heartbeat comments and splits CRLF frames", async () => {
    const onNudge = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse([": ping\n\n", "event: notify\r\n\r\n"]),
    );
    await openNotificationStream("t", onNudge, new AbortController().signal);
    expect(onNudge).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });

  it("handles a frame split across two reads", async () => {
    const onNudge = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      streamResponse(["event: not", "ify\ndata: \n\n"]),
    );
    await openNotificationStream("t", onNudge, new AbortController().signal);
    expect(onNudge).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });

  it("throws on a non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("nope", { status: 401 }));
    await expect(openNotificationStream("t", vi.fn(), new AbortController().signal)).rejects.toThrow();
    vi.restoreAllMocks();
  });
});

describe("sleepWithSignal", () => {
  it("resolves early on abort", async () => {
    const ac = new AbortController();
    const p = sleepWithSignal(10_000, ac.signal);
    ac.abort();
    await expect(p).resolves.toBeUndefined();
  });
});
