import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, test } from "vitest";
import { AuthProvider, safeReturnTo, useAuth } from "./auth";

function Probe() {
  const { ready, token } = useAuth();
  return (
    <div>
      ready:{String(ready)} token:{token ?? "none"}
    </div>
  );
}

test("AuthProvider exposes auth context to children", async () => {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await waitFor(() => expect(screen.getByText(/ready:true/)).toBeInTheDocument());
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
});

describe("safeReturnTo", () => {
  it("passes a same-origin absolute path (with query) through", () => {
    expect(safeReturnTo("/settings/notifications")).toBe("/settings/notifications");
    expect(safeReturnTo("/capa?capa=c1")).toBe("/capa?capa=c1");
  });
  it("rejects a protocol-relative or absolute URL → /", () => {
    expect(safeReturnTo("//evil.com")).toBe("/");
    expect(safeReturnTo("https://evil.com/x")).toBe("/");
    expect(safeReturnTo("/\\evil.com")).toBe("/");
  });
  it("rejects non-path / missing values → /", () => {
    expect(safeReturnTo(undefined)).toBe("/");
    expect(safeReturnTo("")).toBe("/");
    expect(safeReturnTo("relative/path")).toBe("/");
    expect(safeReturnTo(42)).toBe("/");
  });
});
