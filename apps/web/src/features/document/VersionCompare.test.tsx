import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useLocation, useNavigate } from "react-router-dom";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { versionFixture } from "../../test/msw/handlers";
import { VersionCompare } from "./VersionCompare";
import type { DocumentVersion } from "../../lib/types";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";
const OLDER = "ffff1111-1111-1111-1111-111111111111";
const versions = versionFixture as unknown as DocumentVersion[];
const selectorVersions: DocumentVersion[] = [
  ...versions,
  {
    ...versions[1]!,
    id: OLDER,
    version_seq: 0,
    revision_label: "Rev 0",
  },
];

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output aria-label="Current location">{pathname + search}</output>;
}

function CompareNavigation() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/documents/${DOC}?from=${FROM}&to=${TO}`)}>
        external valid pair
      </button>
      <button onClick={() => navigate(`/documents/${DOC}?from=unknown-version&to=${TO}`)}>
        external invalid pair
      </button>
      <button onClick={() => navigate(`/documents/${DOC}?from=${TO}&to=${TO}`)}>
        external equal pair
      </button>
      <button onClick={() => navigate(`/documents/${DOC}`)}>external cleared pair</button>
      <button onClick={() => navigate(`/documents/${DOC}?from=${FROM}&to=${TO}&mode=visual`)}>
        external visual mode
      </button>
      <button onClick={() => navigate(`/documents/${DOC}?from=${FROM}&to=${TO}`)}>
        external text mode
      </button>
      <VersionCompare documentId={DOC} versions={versions} />
    </>
  );
}

function CompareHistoryControls() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  return (
    <>
      <button
        onClick={() => {
          const params = new URLSearchParams(search);
          params.set("checkpoint", "prepared");
          navigate(`${pathname}?${params}`);
        }}
      >
        Prepare history
      </button>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}

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

test("VersionCompare defaults incomplete and invalid cold pairs without forwarding unsafe IDs", async () => {
  const requests: string[] = [];
  server.use(
    http.get("/api/v1/documents/:id/versions/:vid/diff", ({ request }) => {
      requests.push(request.url);
      return HttpResponse.json({
        from: { revision_label: "Rev A" },
        to: { revision_label: "Rev B", change_reason: null },
        metadata_diff: [],
        text_diff: { status: "ok", hunks: [] },
      });
    }),
  );
  renderWithProviders(
    <>
      <VersionCompare documentId={DOC} versions={versions} />
      <LocationProbe />
    </>,
    { route: `/documents/${DOC}?from=unknown-version&to=${TO}` },
  );

  expect(await screen.findByText("No text changes between these versions.")).toBeInTheDocument();
  expect(requests).toEqual([expect.stringContaining(`/versions/${TO}/diff?from=${FROM}`)]);
  expect(requests.join(" ")).not.toContain("unknown-version");
  expect(screen.getByLabelText("Current location")).toHaveTextContent("from=unknown-version");
});

test("VersionCompare defaults a cold pair with one missing side", async () => {
  renderWithProviders(<VersionCompare documentId={DOC} versions={versions} />, {
    route: `/documents/${DOC}?from=${FROM}`,
  });
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
});

test.each([`mode=visual&mode=unknown-sentinel`, `mode=unknown-sentinel&mode=visual`])(
  "conflicting duplicate document modes resolve to the safe text viewer for %s",
  async (search) => {
    const { container } = renderWithProviders(
      <VersionCompare documentId={DOC} versions={versions} />,
      { route: `/documents/${DOC}?from=${FROM}&to=${TO}&${search}` },
    );

    expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
    expect(screen.queryByText("Page images")).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("unknown-sentinel");
  },
);

test.each([`from=${OLDER}&from=${FROM}&to=${TO}`, `from=${FROM}&from=${OLDER}&to=${TO}`])(
  "conflicting duplicate comparison selectors use the safe default pair for %s",
  async (search) => {
    const requests: string[] = [];
    server.use(
      http.get("/api/v1/documents/:id/versions/:vid/diff", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json({
          from: { revision_label: "Rev A" },
          to: { revision_label: "Rev B", change_reason: null },
          metadata_diff: [],
          text_diff: { status: "ok", hunks: [] },
        });
      }),
    );

    renderWithProviders(<VersionCompare documentId={DOC} versions={selectorVersions} />, {
      route: `/documents/${DOC}?${search}`,
    });

    expect(await screen.findByText("No text changes between these versions.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Compare from" })).toHaveValue("Rev A · Superseded");
    expect(requests).toEqual([expect.stringContaining(`/versions/${TO}/diff?from=${FROM}`)]);
  },
);

test("VersionCompare changes to valid, fallback, equal, cleared, and visual live URL state", async () => {
  const user = userEvent.setup();
  renderWithProviders(<CompareNavigation />, { route: `/documents/${DOC}` });
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "external valid pair" }));
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "external invalid pair" }));
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "external equal pair" }));
  expect(screen.getByText("Pick two different versions to compare.")).toBeInTheDocument();
  expect(screen.queryByText(/Added weighted scoring/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "external cleared pair" }));
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "external visual mode" }));
  expect(await screen.findByText("Page images")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "external text mode" }));
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
});

test("VersionCompare controls replace history and delete the default text mode", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <VersionCompare documentId={DOC} versions={versions} />
      <CompareHistoryControls />
      <LocationProbe />
    </>,
    { route: `/documents/${DOC}?from=${FROM}&to=${TO}&sentinel=keep&checkpoint=baseline` },
  );
  await screen.findByText(/Added weighted scoring/);
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(screen.getByText("Visual"));
  expect(await screen.findByText("Page images")).toBeInTheDocument();
  expect(screen.getByLabelText("Current location")).toHaveTextContent("mode=visual");
  await user.click(screen.getByText("Text"));
  expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("mode=");
  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
  );
});

test.each([
  ["Compare from", `${OLDER}&to=${TO}`, "Rev A · Superseded", `from=${FROM}&to=${TO}`],
  ["to", `${FROM}&to=${OLDER}`, "Rev B · Effective", `from=${FROM}&to=${TO}`],
] as const)(
  "%s selection replaces history while preserving the current comparison state",
  async (label, pair, option, expectedPair) => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <VersionCompare documentId={DOC} versions={selectorVersions} />
        <CompareHistoryControls />
        <LocationProbe />
      </>,
      { route: `/documents/${DOC}?from=${pair}&sentinel=keep&checkpoint=baseline` },
    );
    expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Prepare history" }));
    await user.click(screen.getByRole("textbox", { name: label }));
    await user.click(await screen.findByRole("option", { name: option }));

    expect(await screen.findByText(/Added weighted scoring/)).toBeInTheDocument();
    expect(screen.getByLabelText("Current location")).toHaveTextContent(expectedPair);
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=prepared");
    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
    );
    expect(screen.getByLabelText("Current location")).not.toHaveTextContent("checkpoint=prepared");
  },
);

test.each([
  ["absent", "Compare from", "", "from", OLDER, TO],
  ["absent", "to", "", "to", FROM, OLDER],
  ["one-sided", "Compare from", `to=${OLDER}`, "from", OLDER, TO],
  ["one-sided", "to", `from=${OLDER}`, "to", FROM, OLDER],
  ["invalid", "Compare from", `from=unknown-from-sentinel&to=${OLDER}`, "from", OLDER, TO],
  ["invalid", "to", `from=${OLDER}&to=unknown-to-sentinel`, "to", FROM, OLDER],
] as const)(
  "a first %s URL edit through the %s Select writes a complete safe pair",
  async (_, label, initialPair, direction, expectedFrom, expectedTo) => {
    const user = userEvent.setup();
    const requests: string[] = [];
    server.use(
      http.get("/api/v1/documents/:id/versions/:vid/diff", ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json({
          from: { revision_label: "Rev A" },
          to: { revision_label: "Rev B", change_reason: null },
          metadata_diff: [],
          text_diff: { status: "ok", hunks: [] },
        });
      }),
    );
    const query = [initialPair, "sentinel=keep", "checkpoint=baseline"].filter(Boolean).join("&");
    const { container } = renderWithProviders(
      <>
        <VersionCompare documentId={DOC} versions={selectorVersions} />
        <CompareHistoryControls />
        <LocationProbe />
      </>,
      { route: `/documents/${DOC}?${query}` },
    );

    expect(await screen.findByText("No text changes between these versions.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Compare from" })).toHaveValue("Rev A · Superseded");
    expect(screen.getByRole("textbox", { name: "to" })).toHaveValue("Rev B · Effective");
    expect(requests).toEqual([expect.stringContaining(`/versions/${TO}/diff?from=${FROM}`)]);
    if (initialPair) {
      expect(screen.getByLabelText("Current location")).toHaveTextContent(initialPair);
    } else {
      expect(screen.getByLabelText("Current location")).not.toHaveTextContent("from=");
      expect(screen.getByLabelText("Current location")).not.toHaveTextContent("to=");
    }

    await user.click(screen.getByRole("button", { name: "Prepare history" }));
    await user.click(screen.getByRole("textbox", { name: label }));
    await user.click(await screen.findByRole("option", { name: "Rev 0 · Superseded" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent(`from=${expectedFrom}`),
    );
    expect(screen.getByLabelText("Current location")).toHaveTextContent(`to=${expectedTo}`);
    expect(screen.getByLabelText("Current location")).toHaveTextContent("sentinel=keep");
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=prepared");
    expect(screen.getByRole("textbox", { name: "Compare from" })).toHaveValue(
      direction === "from" ? "Rev 0 · Superseded" : "Rev A · Superseded",
    );
    expect(screen.getByRole("textbox", { name: "to" })).toHaveValue(
      direction === "to" ? "Rev 0 · Superseded" : "Rev B · Effective",
    );
    await waitFor(() =>
      expect(requests.at(-1)).toContain(`/versions/${expectedTo}/diff?from=${expectedFrom}`),
    );
    expect(container).not.toHaveTextContent("unknown-from-sentinel");
    expect(container).not.toHaveTextContent("unknown-to-sentinel");

    await user.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
    );
    expect(screen.getByLabelText("Current location")).not.toHaveTextContent("checkpoint=prepared");
  },
);

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
