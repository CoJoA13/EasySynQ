import type {
  ImportConfidenceBand,
  ImportDisposition,
  ImportKind,
  ImportReviewStatus,
} from "../../lib/types";

// The five queue tabs (mockup order). `countKey` indexes run.counts.queues for the tab badge.
export type IngestionQueue = "needs" | "medium" | "high" | "quarantine" | "vault";
export const QUEUES: { value: IngestionQueue; label: string; countKey: string }[] = [
  { value: "needs", label: "Needs decision", countKey: "needs" },
  { value: "medium", label: "Medium", countKey: "medium" },
  { value: "high", label: "High", countKey: "high" },
  { value: "quarantine", label: "Quarantine", countKey: "quarantine" },
  { value: "vault", label: "Already in vault", countKey: "vault" },
];

export type ConfidenceChoice = ImportConfidenceBand | "ALL";
const CONF_CHOICES: ConfidenceChoice[] = ["ALL", "HIGH", "MEDIUM", "LOW", "AMBIGUOUS"];
const QUEUE_VALUES = QUEUES.map((q) => q.value);

// The server-supported /files filter (the ONLY filterable dimensions; clause/process/type are not).
export interface FilesFilter {
  disposition?: ImportDisposition;
  kind?: ImportKind;
  band?: ImportConfidenceBand;
  review_status?: ImportReviewStatus;
}

export interface RunUrlState {
  queue: IngestionQueue;
  conf: ConfidenceChoice;
  offset: number;
}

export const FILES_PAGE_SIZE = 100;

export function parseRunUrl(p: URLSearchParams): RunUrlState {
  const q = p.get("queue");
  const queue = q && QUEUE_VALUES.includes(q as IngestionQueue) ? (q as IngestionQueue) : "needs";
  const c = p.get("conf");
  const conf = c && CONF_CHOICES.includes(c as ConfidenceChoice) ? (c as ConfidenceChoice) : "ALL";
  const n = Number(p.get("offset") ?? "0");
  const offset = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  return { queue, conf, offset };
}

// Map a queue (+ an optional confidence narrowing) to the server /files filter. "vault" has no clean
// filter in v1 (resolved as a documented partial in Task 7) → an empty filter.
export function queueToFilesQuery(queue: IngestionQueue, conf?: ConfidenceChoice): FilesFilter {
  const base: FilesFilter =
    queue === "needs"
      ? { review_status: "undecided" }
      : queue === "medium"
        ? { band: "MEDIUM" }
        : queue === "high"
          ? { band: "HIGH" }
          : queue === "quarantine"
            ? { disposition: "quarantine" }
            : {};
  if (conf && conf !== "ALL") return { ...base, band: conf };
  return base;
}

export function buildFilesQuery(filter: FilesFilter, page: { limit: number; offset: number }): string {
  const p = new URLSearchParams();
  p.set("limit", String(page.limit));
  p.set("offset", String(page.offset));
  if (filter.disposition) p.set("disposition", filter.disposition);
  if (filter.kind) p.set("kind", filter.kind);
  if (filter.band) p.set("band", filter.band);
  if (filter.review_status) p.set("review_status", filter.review_status);
  return p.toString();
}
