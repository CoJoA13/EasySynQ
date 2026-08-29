import { describe, expect, it } from "vitest";

// A source-text adoption contract, in the idiom of ./responsiveRegisterContract.test.ts.
//
// Without it nothing pins the S-ui-4 adoption: the header renders the same accessible name the
// hand-rolled `<Title>` did, so reverting any one page — or adding a twelfth register that
// hand-rolls its own header again — leaves the whole suite green. The behavioural suites cannot
// close this, because there is no behaviour to observe; the point of the change is that there is
// none. What is worth protecting is the single definition, so the guard is over the source.
const sources = import.meta.glob(
  [
    "../features/audits/AuditsListPage.tsx",
    "../features/capa/CapaBoardPage.tsx",
    "../features/capa/ComplaintsPage.tsx",
    "../features/capa/NcrsPage.tsx",
    "../features/context/ContextRegisterPage.tsx",
    "../features/dcr/DcrsRegisterPage.tsx",
    "../features/improvement/ImprovementRegisterPage.tsx",
    "../features/interested-parties/InterestedPartiesRegisterPage.tsx",
    "../features/management-review/ManagementReviewsRegisterPage.tsx",
    "../features/objectives/ObjectivesRegisterPage.tsx",
    "../features/risk/RisksRegisterPage.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
) as Record<string, string>;

const ADOPTERS = [
  "features/audits/AuditsListPage.tsx",
  "features/capa/CapaBoardPage.tsx",
  "features/capa/ComplaintsPage.tsx",
  "features/capa/NcrsPage.tsx",
  "features/context/ContextRegisterPage.tsx",
  "features/dcr/DcrsRegisterPage.tsx",
  "features/improvement/ImprovementRegisterPage.tsx",
  "features/interested-parties/InterestedPartiesRegisterPage.tsx",
  "features/management-review/ManagementReviewsRegisterPage.tsx",
  "features/objectives/ObjectivesRegisterPage.tsx",
  "features/risk/RisksRegisterPage.tsx",
] as const;

function sourceFor(path: string): string {
  const source = Object.entries(sources).find(([key]) => key.endsWith(path))?.[1];
  if (typeof source !== "string") throw new Error(`Missing register source: ${path}`);
  return source;
}

describe("shared register header adoption contract", () => {
  it("covers the eleven adopters S-ui-4 converted", () => {
    expect(ADOPTERS).toHaveLength(11);
  });

  it.each(ADOPTERS)("%s renders its page title through the shared header", (path) => {
    const source = sourceFor(path);
    expect(source).toContain('import { RegisterPageHeader } from "../../lib/RegisterPageHeader";');

    // One header per branch: forbidden, error, and loaded. A page that grows a fourth rest state
    // should render the header there too rather than dropping the title, which is the defect the
    // loading branches still carry (RES-REGISTER-PAGE-FRAME).
    expect(source.match(/<RegisterPageHeader[\s/]/g)).toHaveLength(3);

    // No page keeps a hand-rolled page title beside the shared one. `<Title>` is not banned from
    // the SPA — only from these eleven files, where it was the thing being centralised.
    expect(source).not.toMatch(/<Title[\s>]/);

    // The freshness stamp comes through the header now, not as a loose sibling.
    expect(source).not.toContain("<AsOf");
  });
});
