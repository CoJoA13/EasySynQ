import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useAudit, useAuditPrograms, useAudits, useFindings, useProcesses } from "./hooks";

function AuditsProbe() {
  const { data, forbidden } = useAudits();
  if (forbidden) return <div>forbidden</div>;
  return <div>{(data ?? []).map((a) => a.identifier).join(",")}</div>;
}

test("useAudits unwraps {data} and surfaces rows", async () => {
  renderWithProviders(<AuditsProbe />);
  expect(await screen.findByText(/REC-000061/)).toBeInTheDocument();
});

test("useAudits surfaces a forbidden flag on 403", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<AuditsProbe />);
  expect(await screen.findByText("forbidden")).toBeInTheDocument();
});

function DetailProbe({ id }: { id: string | null }) {
  const { data } = useAudit(id);
  return <div>{data?.title ?? "none"}</div>;
}

test("useAudit fetches the detail; disabled while id is null", async () => {
  renderWithProviders(<DetailProbe id="au000001-0001-0001-0001-000000000001" />);
  expect(await screen.findByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  renderWithProviders(<DetailProbe id={null} />);
  expect(screen.getByText("none")).toBeInTheDocument();
});

function ProgramsProbe() {
  const { data } = useAuditPrograms();
  return <div>{(data ?? []).map((p) => p.identifier).join(",")}</div>;
}

test("useAuditPrograms unwraps {data}", async () => {
  renderWithProviders(<ProgramsProbe />);
  expect(await screen.findByText(/AUDPROG-000001/)).toBeInTheDocument();
});

function FindingsProbe() {
  const { data, forbidden } = useFindings("au000001-0001-0001-0001-000000000001");
  if (forbidden) return <div>findings-forbidden</div>;
  return <div>{(data ?? []).map((f) => f.identifier).join(",")}</div>;
}

test("useFindings unwraps {data}; forbidden flag on 403", async () => {
  renderWithProviders(<FindingsProbe />);
  expect(await screen.findByText(/REC-000062/)).toBeInTheDocument();
  server.use(
    http.get("/api/v1/audits/:id/findings", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<FindingsProbe />);
  expect(await screen.findByText("findings-forbidden")).toBeInTheDocument();
});

function ProcessesProbe() {
  const { data, forbidden } = useProcesses();
  if (forbidden) return <div>proc-forbidden</div>;
  return <div>{(data ?? []).map((p) => p.name).join(",")}</div>;
}

test("useProcesses reads the bare array; degrades on 403", async () => {
  renderWithProviders(<ProcessesProbe />);
  expect(await screen.findByText(/Purchasing/)).toBeInTheDocument();
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProcessesProbe />);
  expect(await screen.findByText("proc-forbidden")).toBeInTheDocument();
});
