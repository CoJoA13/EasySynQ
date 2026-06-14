import { describe, expect, it } from "vitest";
import type { DocumentVersion } from "../../lib/types";
import { resolvePredecessor } from "./resolvePredecessor";

function v(over: Partial<DocumentVersion> & { id: string; version_seq: number }): DocumentVersion {
  return {
    document_id: "doc",
    revision_label: `Rev ${over.version_seq}`,
    version_state: "Effective",
    change_significance: "MAJOR",
    change_reason: "",
    source_blob_sha256: "sha",
    metadata_snapshot: null,
    author_user_id: "u",
    effective_from: null,
    effective_to: null,
    superseded_by_version_id: null,
    created_at: null,
    ...over,
  };
}

describe("resolvePredecessor", () => {
  it("prefers the exact succession link (the version whose superseded_by points at the resulting one)", () => {
    const versions = [
      v({ id: "new", version_seq: 2 }),
      v({
        id: "old",
        version_seq: 1,
        superseded_by_version_id: "new",
        version_state: "Superseded",
      }),
    ];
    expect(resolvePredecessor(versions, "new")).toEqual({ from: "old", to: "new" });
  });

  it("falls back to the immediate version_seq predecessor pre-cutover (no succession link yet)", () => {
    const versions = [
      v({ id: "new", version_seq: 2, version_state: "Approved" }),
      v({ id: "eff", version_seq: 1, version_state: "Effective" }),
    ];
    expect(resolvePredecessor(versions, "new")).toEqual({ from: "eff", to: "new" });
  });

  it("resolves the predecessor of the GIVEN resulting version even when a later revision exists", () => {
    const versions = [
      v({ id: "newest", version_seq: 3 }),
      v({ id: "mid", version_seq: 2 }),
      v({ id: "first", version_seq: 1 }),
    ];
    expect(resolvePredecessor(versions, "mid")).toEqual({ from: "first", to: "mid" });
  });

  it("returns null when the resulting version is absent from the list", () => {
    expect(resolvePredecessor([v({ id: "a", version_seq: 1 })], "missing")).toBeNull();
  });

  it("returns null when the resulting version has no predecessor", () => {
    expect(resolvePredecessor([v({ id: "only", version_seq: 1 })], "only")).toBeNull();
  });
});
