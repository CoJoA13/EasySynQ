import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { versionFixture } from "../../test/msw/handlers";
import { VersionCompare } from "./VersionCompare";
import type { DocumentVersion } from "../../lib/types";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";
const versions = versionFixture as unknown as DocumentVersion[];

test("VersionCompare renders the redline once a distinct pair is in the URL", async () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${FROM}&to=${TO}`,
  });
  await waitFor(() => expect(screen.getByText(/Added weighted scoring/)).toBeInTheDocument());
});

test("VersionCompare defaults to the prior → newest pair on a cold visit (no URL params)", async () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}`,
  });
  // with no ?from/?to, the redline defaults to Rev A → Rev B and renders immediately
  await waitFor(() => expect(screen.getByText(/Added weighted scoring/)).toBeInTheDocument());
});

test("VersionCompare guards against comparing a version with itself", () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${TO}&to=${TO}`,
  });
  expect(screen.getByText("Pick two different versions to compare.")).toBeInTheDocument();
  expect(screen.queryByText(/Added weighted scoring/)).not.toBeInTheDocument();
});

test("VersionCompare is hidden when there is nothing to compare (<2 versions)", () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions.slice(0, 1)} />, {
    route: `/documents/${DOC}`,
  });
  expect(screen.queryByText("Compare from")).not.toBeInTheDocument();
});

test("VersionCompare defaults to the Text redline and exposes a Text|Visual switch", async () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${FROM}&to=${TO}`,
  });
  await screen.findByText(/Added weighted scoring/); // text redline (default mode)
  expect(screen.getByText("Text")).toBeInTheDocument();
  expect(screen.getByText("Visual")).toBeInTheDocument();
});

test("VersionCompare switches to the visual diff via the mode toggle", async () => {
  const user = userEvent.setup();
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${FROM}&to=${TO}`,
  });
  await screen.findByText(/Added weighted scoring/);
  await user.click(screen.getByText("Visual"));
  await screen.findByText("Page images"); // the visual viewer mounted
  expect(screen.queryByText(/Added weighted scoring/)).not.toBeInTheDocument();
});

test("VersionCompare honours ?mode=visual from the URL (deep-link), keeping the pair", async () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${FROM}&to=${TO}&mode=visual`,
  });
  await screen.findByText("Page images");
  // the same pair drives the visual viewer — its changed-page rail is present
  expect(screen.getByRole("button", { name: "Page 2, changed" })).toBeInTheDocument();
  expect(screen.queryByText(/Added weighted scoring/)).not.toBeInTheDocument();
});

test("VersionCompare has no a11y violations (text mode)", async () => {
  const { container } = renderWithProviders(
    <VersionCompare documentId={DOC} versions={versions} />,
    { route: `/documents/${DOC}?from=${FROM}&to=${TO}` },
  );
  await screen.findByText(/Added weighted scoring/);
  expect(await axe(container)).toHaveNoViolations();
});

test("VersionCompare has no a11y violations (visual mode)", async () => {
  const { container } = renderWithProviders(
    <VersionCompare documentId={DOC} versions={versions} />,
    { route: `/documents/${DOC}?from=${FROM}&to=${TO}&mode=visual` },
  );
  await screen.findByAltText(/Diff layer/);
  expect(await axe(container)).toHaveNoViolations();
});
