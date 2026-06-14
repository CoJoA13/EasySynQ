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

  it("uses the governing Effective version pre-cutover (resulting still Approved, no succession link)", () => {
    const versions = [
      v({ id: "new", version_seq: 2, version_state: "Approved" }),
      v({ id: "eff", version_seq: 1, version_state: "Effective" }),
    ];
    expect(resolvePredecessor(versions, "new")).toEqual({ from: "eff", to: "new" });
  });

  it("pre-cutover, picks the Effective version, NOT an abandoned higher-seq Draft (Codex P2)", () => {
    // A changes-requested loop left an abandoned Draft (seq 2) between the Effective version (seq 1)
    // and the approved resulting version (seq 3). The diff must compare against the governing
    // Effective version, never the rejected draft.
    const versions = [
      v({ id: "resulting", version_seq: 3, version_state: "Approved" }),
      v({ id: "abandoned", version_seq: 2, version_state: "Draft" }),
      v({ id: "eff", version_seq: 1, version_state: "Effective" }),
    ];
    expect(resolvePredecessor(versions, "resulting")).toEqual({ from: "eff", to: "resulting" });
  });

  it("resolves a non-newest resulting version's predecessor via the succession link (post-cutover)", () => {
    // A later revision (newest, seq 3) is now Effective; the resulting version under view (mid, seq 2)
    // is Superseded and points back to the version IT superseded (first). The succession link, not the
    // raw seq order, must drive the pair.
    const versions = [
      v({ id: "newest", version_seq: 3, version_state: "Effective" }),
      v({
        id: "mid",
        version_seq: 2,
        version_state: "Superseded",
        superseded_by_version_id: "newest",
      }),
      v({
        id: "first",
        version_seq: 1,
        version_state: "Superseded",
        superseded_by_version_id: "mid",
      }),
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
