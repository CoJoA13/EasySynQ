import { describe, expect, it } from "vitest";
import { toRoutePath } from "./deepLink";

describe("toRoutePath", () => {
  it("strips the origin from a document deep link", () => {
    expect(toRoutePath("http://localhost/documents/abc")).toBe("/documents/abc");
  });
  it("preserves the query string for drawer-style links", () => {
    expect(toRoutePath("http://localhost/capa?capa=c1")).toBe("/capa?capa=c1");
    expect(toRoutePath("http://localhost/dcrs?dcr=d1")).toBe("/dcrs?dcr=d1");
    expect(toRoutePath("http://localhost/improvement?initiative=i1")).toBe(
      "/improvement?initiative=i1",
    );
  });
  it("handles the /tasks fallback link and a deployed host", () => {
    expect(toRoutePath("http://localhost/tasks")).toBe("/tasks");
    expect(toRoutePath("https://qms.example.org/management-reviews/m1")).toBe(
      "/management-reviews/m1",
    );
  });
  it("falls back to /tasks on a malformed or empty link", () => {
    expect(toRoutePath("not a url")).toBe("/tasks");
    expect(toRoutePath("")).toBe("/tasks");
  });
});
