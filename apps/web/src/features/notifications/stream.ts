// apps/web/src/features/notifications/stream.ts
// SSE primitives for the notification bell (S-notify-5c). The fetch/ReadableStream reader carries the
// in-memory bearer on the Authorization header (native EventSource can't set headers; no token in URL).

export function parseSseFrame(frame: string): { event: string; data: string } {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // comment / heartbeat
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, data: data.join("\n") };
}

// Resolves when the server closes the stream cleanly; rejects on a network/HTTP error; aborts via signal.
export async function openNotificationStream(
  token: string,
  onNudge: () => void,
  signal: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/v1/notifications/stream", {
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
    signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    const sep = /\r?\n\r?\n/g; // SSE frame boundary, CRLF- or LF-delimited (server emits LF)
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = sep.exec(buf)) !== null) {
      const { event } = parseSseFrame(buf.slice(last, m.index));
      last = m.index + m[0].length;
      if (event === "notify") onNudge();
    }
    buf = buf.slice(last); // keep the partial trailing frame across reads
  }
}

// A setTimeout that also resolves immediately when the signal aborts — so an unmount during the
// reconnect backoff window cancels the pending reconnect (no zombie loop).
export function sleepWithSignal(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      resolve();
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
