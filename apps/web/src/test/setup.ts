import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { toHaveNoViolations } from "jest-axe";
import { afterAll, afterEach, beforeAll, expect } from "vitest";
import { server } from "./msw/server";

// jsdom does not implement window.matchMedia; Mantine requires it.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom's Blob/File don't implement arrayBuffer() in this env; the authoring SHA-256 hash
// (lib/hash.ts) needs it. Delegate to FileReader (which jsdom does implement). Production browsers
// have the native method, so this only fills the test gap (the matchMedia/ResizeObserver precedent).
if (typeof Blob !== "undefined" && typeof Blob.prototype.arrayBuffer !== "function") {
  Blob.prototype.arrayBuffer = function (this: Blob) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

// jsdom implements neither URL.createObjectURL nor revokeObjectURL; the S-web-4b visual-diff
// viewer fetches each page PNG → Blob → objectURL → <img src> and revokes on cleanup. A fixed
// stub URL is enough for the tests (they assert the authed fetch + the <img> alt, not the pixels).
URL.createObjectURL = (() => "blob:mock") as unknown as typeof URL.createObjectURL;
URL.revokeObjectURL = (() => {}) as unknown as typeof URL.revokeObjectURL;

// jsdom lacks ResizeObserver; Mantine's FloatingIndicator (SegmentedControl / Tabs) needs it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

// jsdom doesn't implement Element.scrollIntoView; Mantine's Combobox scrolls the active option into
// view on a timer when a dropdown opens. Left unstubbed, that timer throws "scrollIntoView is not a
// function" AFTER a test that leaves a Select open tears down → an unhandled error (false-positive risk).
// A no-op stub fills the gap (the matchMedia/ResizeObserver precedent).
if (typeof Element !== "undefined" && typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = function () {};
}

expect.extend(toHaveNoViolations);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
