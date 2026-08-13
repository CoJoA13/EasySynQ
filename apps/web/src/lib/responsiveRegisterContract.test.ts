import { describe, expect, it } from "vitest";

const sources = import.meta.glob(
  [
    "../features/review/TasksInbox.tsx",
    "../features/audits/AuditsListPage.tsx",
    "../features/dcr/DcrsRegisterPage.tsx",
    "../features/objectives/ObjectivesRegisterPage.tsx",
    "../features/management-review/ManagementReviewsRegisterPage.tsx",
    "../features/improvement/ImprovementRegisterPage.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
) as Record<string, string>;

const contracts = [
  ["features/review/TasksInbox.tsx", 720],
  ["features/audits/AuditsListPage.tsx", 800],
  ["features/dcr/DcrsRegisterPage.tsx", 1040],
  ["features/objectives/ObjectivesRegisterPage.tsx", 720],
  ["features/management-review/ManagementReviewsRegisterPage.tsx", 800],
  ["features/improvement/ImprovementRegisterPage.tsx", 920],
] as const;

function sourceFor(path: string): string {
  const source = Object.entries(sources).find(([key]) => key.endsWith(path))?.[1];
  if (typeof source !== "string") throw new Error(`Missing responsive-register source: ${path}`);
  return source;
}

describe("responsive shared-register source contract", () => {
  it.each(contracts)("keeps one %s table in its %i px owner", (path, minWidth) => {
    const source = sourceFor(path);
    expect(source.match(/<Table\.ScrollContainer/g)).toHaveLength(1);
    expect(source).toContain(`<Table.ScrollContainer minWidth={${minWidth}}>`);
    expect(source.match(/<Table(?:\s|>)/g)).toHaveLength(1);
    expect(source).not.toMatch(/visibleFrom=|hiddenFrom=/);
  });
});
