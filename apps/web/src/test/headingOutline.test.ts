import { afterEach, describe, expect, it } from "vitest";
import { expectSoundHeadingOutline, readHeadingOutline } from "./headingOutline";

// Hosts are appended to document.body so the default-root case can be exercised for real. The
// global `cleanup()` only removes Testing Library's own containers, so these must be tracked and
// removed here — otherwise every fixture accumulates and the default-root test reads the union of
// every heading the file has ever mounted.
const mounted: HTMLElement[] = [];
afterEach(() => {
  while (mounted.length > 0) mounted.pop()?.remove();
});

function mount(html: string): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.append(host);
  mounted.push(host);
  return host;
}

describe("readHeadingOutline", () => {
  it("reads every heading in document order, not selector order", () => {
    const host = mount("<h2>Second</h2><h1>First by tag, second in the DOM</h1><h3>Third</h3>");
    expect(readHeadingOutline(host)).toEqual([
      { level: 2, text: "Second" },
      { level: 1, text: "First by tag, second in the DOM" },
      { level: 3, text: "Third" },
    ]);
  });

  it("prefers an explicit aria-level over the tag, and reads a role=heading with no tag", () => {
    const host = mount(
      '<h2 aria-level="1">Promoted</h2><div role="heading" aria-level="2">Div heading</div>',
    );
    expect(readHeadingOutline(host)).toEqual([
      { level: 1, text: "Promoted" },
      { level: 2, text: "Div heading" },
    ]);
  });

  it("skips a heading hidden from the accessibility tree, including a nested one", () => {
    const host = mount(
      '<h1>Visible</h1><div aria-hidden="true"><h2>Hidden wrapper child</h2></div>' +
        '<h3 aria-hidden="true">Hidden itself</h3>',
    );
    expect(readHeadingOutline(host)).toEqual([{ level: 1, text: "Visible" }]);
  });

  it("skips a `hidden` heading too, so this agrees with axe's own heading-order", () => {
    const host = mount("<h1>Visible</h1><section hidden><h2>Hidden section</h2></section>");
    expect(readHeadingOutline(host)).toEqual([{ level: 1, text: "Visible" }]);
  });

  it("drops a role=heading carrying no level at all rather than reporting NaN", () => {
    const host = mount('<h1>Real</h1><div role="heading">No level</div>');
    expect(readHeadingOutline(host)).toEqual([{ level: 1, text: "Real" }]);
  });
});

describe("expectSoundHeadingOutline", () => {
  it("accepts a sound outline that descends one level at a time", () => {
    const host = mount("<h1>Page</h1><h2>Section</h2><h3>Subsection</h3>");
    expect(expectSoundHeadingOutline(host)).toHaveLength(3);
  });

  it("accepts ASCENDING any distance — closing an h3 subsection to open the next h2", () => {
    const host = mount("<h1>Page</h1><h2>One</h2><h3>Deep</h3><h4>Deeper</h4><h2>Two</h2>");
    expect(() => expectSoundHeadingOutline(host)).not.toThrow();
  });

  // The three failure modes below are the reason this helper exists. Each one passes every axe
  // gate in the repository, so if any of these three stopped throwing the gate would be inert
  // again without a single test turning red.
  it("FAILS when a page renders no h1 at all — the 26-route defect", () => {
    const host = mount("<h2>Register</h2><h3>Section</h3>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(/expected exactly one h1, found 0/);
  });

  it("FAILS when a page renders two h1s", () => {
    const host = mount("<h1>One</h1><h1>Two</h1>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(/expected exactly one h1, found 2/);
  });

  // The BOUNDARY case: exactly one level skipped. The h1 -> h4 case below cannot pin it — loosening
  // the rule from "descend by at most 1" to "at most 2" leaves a three-level jump still failing, so
  // the assertion named for the boundary has to BE the boundary.
  it("FAILS on the smallest possible skip — h1 straight to h3", () => {
    const host = mount("<h1>Page</h1><h3>Section</h3>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(
      /heading level skipped: h1 "Page" is followed by h3 "Section"/,
    );
  });

  it("FAILS on the h1 to h4 jump measured on /admin/users", () => {
    const host = mount("<h1>Administration</h1><h4>Users</h4>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(
      /heading level skipped: h1 "Administration" is followed by h4 "Users"/,
    );
  });

  it("FAILS when an h2 is rendered ABOVE the h1, which the pairwise check alone cannot see", () => {
    const host = mount("<h2>Above</h2><h1>Page</h1>");
    // Ascending h2 -> h1 is legal pairwise, and there is exactly one h1, so only the
    // first-heading assertion can catch this arrangement.
    expect(() => expectSoundHeadingOutline(host)).toThrow(/the first heading is not the h1/);
  });

  it("names the whole outline in the failure, so a thirty-file re-levelling is reviewable", () => {
    const host = mount("<h1>Page</h1><h3>Jumped</h3>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(/h1 {2}Page[\s\S]*h3 {2}Jumped/);
  });

  // Both helpers default their root to `document.body`, and that default is what all 14 route test
  // files rely on by calling with no argument. Every other test here passes an explicit host, so
  // repointing the default was invisible to this file while reddening those fourteen.
  it("defaults its root to document.body, which is how every route test calls it", () => {
    mount("<h1>Page</h1><h2>Section</h2>");
    expect(readHeadingOutline()).toEqual([
      { level: 1, text: "Page" },
      { level: 2, text: "Section" },
    ]);
    expect(() => expectSoundHeadingOutline()).not.toThrow();
  });

  it("says so plainly when a page renders no headings at all", () => {
    const host = mount("<p>No headings here</p>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(/\(no headings rendered\)/);
  });

  it("reports an empty heading as (empty) rather than rendering a blank line", () => {
    const host = mount("<h1></h1><h3>After</h3>");
    expect(() => expectSoundHeadingOutline(host)).toThrow(/h1 {2}\(empty\)/);
  });
});
