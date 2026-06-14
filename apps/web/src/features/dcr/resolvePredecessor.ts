import type { DocumentVersion } from "../../lib/types";

// S-dcr-ui-3: pin a DCR's diff to (predecessor → resulting). The resulting version's predecessor is
// the version it supersedes — known exactly post-cutover via superseded_by_version_id, and pre-cutover
// it is the GOVERNING Effective version (the one the resulting version will supersede). Returns null
// when the resulting version isn't in the list or has no predecessor (a non-REVISE / first version).
export function resolvePredecessor(
  versions: DocumentVersion[],
  resultingVersionId: string,
): { from: string; to: string } | null {
  const resulting = versions.find((v) => v.id === resultingVersionId);
  if (!resulting) return null;

  // Exact succession link (set at cutover): the version this one supersedes.
  const bySuccession = versions.find((v) => v.superseded_by_version_id === resultingVersionId);
  if (bySuccession) return { from: bySuccession.id, to: resulting.id };

  // Pre-cutover: the GOVERNING Effective version the resulting version will supersede. Prefer it over
  // a raw version_seq predecessor — a changes-requested loop can leave an abandoned Draft at a higher
  // seq than the Effective version, and diffing against that rejected draft (rather than the governing
  // version) would show the wrong redline/visual until cutover (Codex P2). INV-1 (single Effective per
  // document) guarantees at most one match.
  const effective = versions.find(
    (v) => v.version_state === "Effective" && v.id !== resultingVersionId,
  );
  if (effective) return { from: effective.id, to: resulting.id };

  // Degenerate last resort (no Effective version present): the immediate version_seq predecessor.
  const earlier = versions.filter((v) => v.version_seq < resulting.version_seq);
  if (earlier.length === 0) return null;
  const prev = earlier.reduce((a, b) => (b.version_seq > a.version_seq ? b : a));
  return { from: prev.id, to: resulting.id };
}
