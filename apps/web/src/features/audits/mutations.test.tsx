import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useAdvanceAudit, useCreateAudit, useCreateFinding, useCreateProgram } from "./mutations";

let advance: ReturnType<typeof useAdvanceAudit>;
function AdvanceProbe({ auditId }: { auditId: string }) {
  advance = useAdvanceAudit(auditId);
  return <div>{advance.isError ? "error" : (advance.data?.state ?? "idle")}</div>;
}

test("useAdvanceAudit POSTs the transition sub-resource", async () => {
  let hit = "";
  server.use(
    http.post("/api/v1/audits/:id/begin-closing", ({ params }) => {
      hit = String(params.id);
      return HttpResponse.json({ state: "Closing" });
    }),
  );
  renderWithProviders(<AdvanceProbe auditId="au000001-0001-0001-0001-000000000001" />);
  act(() => advance.mutate("begin-closing"));
  await waitFor(() => expect(hit).toBe("au000001-0001-0001-0001-000000000001"));
});

test("a 409 (audit_close_blocked) surfaces as an error — and still refetches (onSettled)", async () => {
  server.use(
    http.post("/api/v1/audits/:id/close", () =>
      HttpResponse.json(
        { code: "audit_close_blocked", title: "Cannot close: 1 live NC finding(s) without a Closed CAPA" },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<AdvanceProbe auditId="au000003-0003-0003-0003-000000000003" />);
  act(() => advance.mutate("close"));
  expect(await screen.findByText("error")).toBeInTheDocument();
});

let createFinding: ReturnType<typeof useCreateFinding>;
function FindingProbe() {
  createFinding = useCreateFinding("au000001-0001-0001-0001-000000000001");
  return <div>{createFinding.data?.auto_capa_id ?? "idle"}</div>;
}

test("useCreateFinding POSTs and returns the created finding (auto_capa_id on NC)", async () => {
  renderWithProviders(<FindingProbe />);
  act(() => createFinding.mutate({ finding_type: "NC", severity: "Major", summary: "x" }));
  expect(await screen.findByText("ca-auto-00-0000-0000-0000-000000000000")).toBeInTheDocument();
});

let createAudit: ReturnType<typeof useCreateAudit>;
function CreateAuditProbe() {
  createAudit = useCreateAudit();
  return <div>{createAudit.data?.id ?? "idle"}</div>;
}

test("useCreateAudit POSTs /audits", async () => {
  renderWithProviders(<CreateAuditProbe />);
  act(() => createAudit.mutate({ plan_id: "pl000001-0001-0001-0001-000000000001" }));
  expect(await screen.findByText("au-new-00-0000-0000-0000-000000000000")).toBeInTheDocument();
});

let createProgram: ReturnType<typeof useCreateProgram>;
function CreateProgramProbe() {
  createProgram = useCreateProgram();
  return <div>{createProgram.data?.identifier ?? "idle"}</div>;
}

test("useCreateProgram POSTs /audit-programs", async () => {
  renderWithProviders(<CreateProgramProbe />);
  act(() => createProgram.mutate({ title: "New programme" }));
  expect(await screen.findByText("AUDPROG-000003")).toBeInTheDocument();
});
