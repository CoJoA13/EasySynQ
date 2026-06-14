import type { DocumentVersion } from "../../lib/types";

// S-dcr-ui-3: pin a DCR's diff to (predecessor → resulting). The resulting version's predecessor is
// the version it supersedes — known exactly post-cutover via superseded_by_version_id, and resolvable
// pre-cutover (resulting still Approved) as the immediate version_seq predecessor. Returns null when
// the resulting version isn't in the list or has no predecessor (a non-REVISE / first version).
export function resolvePredecessor(
  versions: DocumentVersion[],
  resultingVersionId: string,
): { from: string; to: string } | null {
  const resulting = versions.find((v) => v.id === resultingVersionId);
  if (!resulting) return null;

  // Exact succession link (set at cutover): the version this one supersedes.
  const bySuccession = versions.find((v) => v.superseded_by_version_id === resultingVersionId);
  if (bySuccession) return { from: bySuccession.id, to: resulting.id };

  // Pre-cutover fallback: the highest version_seq strictly below the resulting version's seq.
  const earlier = versions.filter((v) => v.version_seq < resulting.version_seq);
  if (earlier.length === 0) return null;
  const prev = earlier.reduce((a, b) => (b.version_seq > a.version_seq ? b : a));
  return { from: prev.id, to: resulting.id };
}
