import { describe, expect, it } from "vitest";
import { classifyEffectiveView } from "./effectiveView";

function view(pathname: string, search = "") {
  return classifyEffectiveView(pathname, new URLSearchParams(search));
}

describe("classifyEffectiveView", () => {
  it("classifies the Acknowledgements task selector as a distinct material view", () => {
    const tasks = view("/tasks");
    const acknowledgements = view("/tasks", "type=DOC_ACK");

    expect(acknowledgements).toMatchObject({
      title: "EasySynQ — Acknowledgements",
      queryStateClass: "material-view",
      focusOwner: "route-main",
      announcement: "Acknowledgements",
    });
    expect(acknowledgements.chromeKey).not.toBe(tasks.chromeKey);
    expect(acknowledgements.recoveryKey).not.toBe(tasks.recoveryKey);
  });

  it("uses the safe general Tasks view for an unknown task type", () => {
    expect(view("/tasks", "type=not-a-view&q=needle")).toMatchObject({
      title: "EasySynQ — Tasks",
      queryStateClass: "ordinary",
      focusOwner: "route-main",
      announcement: "Tasks",
    });
  });

  it.each([
    ["/library", "detail", "Document details"],
    ["/dcrs", "dcr", "Change request details"],
    ["/capa", "capa", "CAPA details"],
    ["/improvement", "initiative", "Improvement details"],
    ["/context", "issue", "Context issue details"],
    ["/interested-parties", "party", "Interested party details"],
    ["/risks", "risk", "Risk details"],
  ])("classifies %s?%s as a safe detail view", (pathname, selector, label) => {
    const detail = view(pathname, `${selector}=secret-dcr-id`);
    const base = view(pathname);

    expect(detail).toMatchObject({
      title: `EasySynQ — ${label}`,
      queryStateClass: "detail",
      focusOwner: "feature",
      announcement: null,
    });
    expect(detail.chromeKey).not.toBe(base.chromeKey);
    expect(detail.recoveryKey).not.toBe(base.recoveryKey);
    expect(`${detail.title}${detail.announcement ?? ""}`).not.toContain("secret-dcr-id");
  });

  it("keeps detail chrome generic while recovering distinct opaque selectors", () => {
    const first = view("/library", "detail=secret-document-id");
    const second = view("/library", "detail=another-document-id");

    expect(first.chromeKey).toBe(second.chromeKey);
    expect(first.recoveryKey).not.toBe(second.recoveryKey);
    expect(`${first.title}${first.announcement ?? ""}`).not.toContain("secret-document-id");
  });

  it.each(["history", "approvals", "where-used", "acks"])(
    "treats the %s document tab as a recovery-only subview",
    (tab) => {
      const document = view("/documents/doc-a");
      const selectedTab = view("/documents/doc-a", `tab=${tab}`);

      expect(selectedTab).toMatchObject({
        title: "EasySynQ — Document",
        queryStateClass: "subview",
        focusOwner: "none",
        announcement: null,
      });
      expect(selectedTab.chromeKey).toBe(document.chromeKey);
      expect(selectedTab.recoveryKey).not.toBe(document.recoveryKey);
    },
  );

  it("treats explicit document defaults and unknown tabs as the base document view", () => {
    const document = view("/documents/doc-a");
    const overview = view("/documents/doc-a", "tab=overview");
    const unknownTab = view("/documents/doc-a", "tab=not-a-tab");

    expect(overview).toEqual(document);
    expect(unknownTab).toEqual(document);
  });

  it.each(["visual"])("treats the %s document mode as a recovery-only subview", (mode) => {
    const document = view("/documents/doc-a");
    const selectedMode = view("/documents/doc-a", `mode=${mode}`);

    expect(selectedMode.chromeKey).toBe(document.chromeKey);
    expect(selectedMode.recoveryKey).not.toBe(document.recoveryKey);
    expect(selectedMode.queryStateClass).toBe("subview");
  });

  it("treats explicit and unknown document modes as the text default", () => {
    const document = view("/documents/doc-a");

    expect(view("/documents/doc-a", "mode=text")).toEqual(document);
    expect(view("/documents/doc-a", "mode=not-a-mode")).toEqual(document);
  });

  it("uses non-empty document comparison identities only for recovery", () => {
    const document = view("/documents/doc-a");
    const comparison = view("/documents/doc-a", "from=secret-document-id&to=version-b");

    expect(comparison).toMatchObject({
      title: "EasySynQ — Document",
      queryStateClass: "subview",
      focusOwner: "none",
      announcement: null,
    });
    expect(comparison.chromeKey).toBe(document.chromeKey);
    expect(comparison.recoveryKey).not.toBe(document.recoveryKey);
    expect(comparison.title).not.toContain("secret-document-id");
  });

  it.each(["visual"])("treats the %s DCR diff mode as a recovery-only subview", (mode) => {
    const diff = view("/dcrs/dcr-a/diff");
    const selectedMode = view("/dcrs/dcr-a/diff", `mode=${mode}`);

    expect(selectedMode).toMatchObject({
      title: "EasySynQ — Document change request",
      queryStateClass: "subview",
      focusOwner: "none",
      announcement: null,
    });
    expect(selectedMode.chromeKey).toBe(diff.chromeKey);
    expect(selectedMode.recoveryKey).not.toBe(diff.recoveryKey);
  });

  it("treats explicit and unknown DCR diff modes as the text default", () => {
    const diff = view("/dcrs/dcr-a/diff");

    expect(view("/dcrs/dcr-a/diff", "mode=text")).toEqual(diff);
    expect(view("/dcrs/dcr-a/diff", "mode=not-a-mode")).toEqual(diff);
  });

  it("keeps ordinary working state out of chrome and recovery identity", () => {
    const unfiltered = view("/tasks");
    const filtered = view("/tasks", "q=needle&sort=title&offset=20&size=50&state=open");

    expect(filtered).toMatchObject({
      title: "EasySynQ — Tasks",
      queryStateClass: "ordinary",
      focusOwner: "route-main",
      announcement: "Tasks",
    });
    expect(filtered.chromeKey).toBe(unfiltered.chromeKey);
    expect(filtered.recoveryKey).toBe(unfiltered.recoveryKey);
  });

  it.each([
    ["material task type", "/tasks", "type=DOC_ACK", "type=DOC_ACK&type=DOC_ACK"],
    ["library detail", "/library", "detail=document-a", "detail=document-a&detail=document-a"],
    ["document tab", "/documents/doc-a", "tab=history", "tab=history&tab=history"],
    ["document mode", "/documents/doc-a", "mode=visual", "mode=visual&mode=visual"],
    [
      "document comparison selectors",
      "/documents/doc-a",
      "from=version-a&to=version-b",
      "from=version-a&from=version-a&to=version-b&to=version-b",
    ],
    ["DCR diff mode", "/dcrs/dcr-a/diff", "mode=visual", "mode=visual&mode=visual"],
  ])("treats identical repeated %s values as one selector", (_name, pathname, single, repeated) => {
    expect(view(pathname, repeated)).toEqual(view(pathname, single));
  });

  it.each([
    [
      "/tasks",
      "type=DOC_ACK&type=not-a-view",
      "type=not-a-view&type=DOC_ACK",
      view("/tasks", "type=not-a-view"),
    ],
    [
      "/library",
      "detail=document-a&detail=document-b",
      "detail=document-b&detail=document-a",
      view("/library"),
    ],
    ["/dcrs", "dcr=dcr-a&dcr=dcr-b", "dcr=dcr-b&dcr=dcr-a", view("/dcrs")],
    ["/capa", "capa=capa-a&capa=capa-b", "capa=capa-b&capa=capa-a", view("/capa")],
    [
      "/improvement",
      "initiative=initiative-a&initiative=initiative-b",
      "initiative=initiative-b&initiative=initiative-a",
      view("/improvement"),
    ],
    ["/context", "issue=issue-a&issue=issue-b", "issue=issue-b&issue=issue-a", view("/context")],
    [
      "/interested-parties",
      "party=party-a&party=party-b",
      "party=party-b&party=party-a",
      view("/interested-parties"),
    ],
    ["/risks", "risk=risk-a&risk=risk-b", "risk=risk-b&risk=risk-a", view("/risks")],
  ])(
    "resolves conflicting repeated detail and material selectors safely",
    (pathname, first, reversed, expected) => {
      expect(view(pathname, first)).toEqual(expected);
      expect(view(pathname, reversed)).toEqual(expected);
    },
  );

  it.each([
    ["tab=history&tab=acks", "tab=acks&tab=history", ""],
    ["mode=visual&mode=text", "mode=text&mode=visual", ""],
    ["from=version-a&from=version-b", "from=version-b&from=version-a", ""],
    ["to=version-a&to=version-b", "to=version-b&to=version-a", ""],
  ])("resolves conflicting repeated document selectors safely", (first, reversed, expected) => {
    expect(view("/documents/doc-a", first)).toEqual(view("/documents/doc-a", expected));
    expect(view("/documents/doc-a", reversed)).toEqual(view("/documents/doc-a", expected));
  });

  it("resolves conflicting repeated DCR diff modes safely", () => {
    const diff = view("/dcrs/dcr-a/diff");

    expect(view("/dcrs/dcr-a/diff", "mode=visual&mode=text")).toEqual(diff);
    expect(view("/dcrs/dcr-a/diff", "mode=text&mode=visual")).toEqual(diff);
  });

  it("ignores unknown query state and query parameter order", () => {
    const originalOrder = view("/tasks", "q=needle&unknown=value&sort=title");
    const reordered = view("/tasks", "sort=title&unknown=value&q=needle");

    expect(reordered).toEqual(originalOrder);
    expect(view("/tasks", "future=secret-document-id")).toMatchObject({
      queryStateClass: "ignored",
      title: "EasySynQ — Tasks",
    });
  });

  it.each([
    ["/", "Dashboard"],
    ["/setup", "Setup"],
    ["/admin", "Administration"],
    ["/admin/users", "Administration"],
    ["/admin/roles", "Administration"],
    ["/admin/processes", "Administration"],
    ["/admin/config", "Administration"],
    ["/library", "Library"],
    ["/library/new", "New document"],
    ["/documents/doc-a", "Document"],
    ["/tasks", "Tasks"],
    ["/tasks/task-a", "Task"],
    ["/settings/notifications", "Notification settings"],
    ["/notifications", "Notifications"],
    ["/search", "Search"],
    ["/compliance", "Compliance"],
    ["/reports/document-control", "Document register"],
    ["/capa", "CAPA"],
    ["/capa/complaints", "Complaints"],
    ["/capa/ncrs", "NCRs"],
    ["/audits", "Audits"],
    ["/audits/programme", "Audit programme"],
    ["/audits/audit-a", "Audit"],
    ["/imports", "Import"],
    ["/imports/run-a", "Import run"],
    ["/ingestion", "Import"],
    ["/ingestion/run-a", "Import run"],
    ["/drift", "Drift"],
    ["/drift/superseded-copies", "Superseded copies"],
    ["/objectives", "Objectives"],
    ["/objectives/objective-a", "Objective"],
    ["/management-reviews", "Management reviews"],
    ["/management-reviews/review-a", "Management review"],
    ["/dcrs", "Document change requests"],
    ["/dcrs/dcr-a/diff", "Document change request"],
    ["/improvement", "Improvement"],
    ["/risks", "Risks"],
    ["/context", "Context"],
    ["/interested-parties", "Interested parties"],
  ])("retains the %s route title", (pathname, label) => {
    expect(view(pathname)).toMatchObject({
      title: `EasySynQ — ${label}`,
      focusOwner: "route-main",
      announcement: label,
    });
  });

  it("returns the fixed not-found description for unmatched routes", () => {
    expect(view("/not-a-route", "detail=secret-document-id")).toMatchObject({
      title: "EasySynQ — Page not found",
      queryStateClass: "ignored",
      focusOwner: "feature",
      announcement: null,
    });
  });
});
