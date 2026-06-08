import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { diffFixture } from "../../test/msw/handlers";
import { RedlineViewer } from "./RedlineViewer";

const DOC = "11111111-1111-1111-1111-111111111111";
const TO = "dddd1111-1111-1111-1111-111111111111";
const FROM = "eeee1111-1111-1111-1111-111111111111";

afterEach(() => vi.restoreAllMocks());

test("RedlineViewer shows the metadata diff (changed rows only) + the change reason", async () => {
  renderWithProviders(<RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() => expect(screen.getByText(/Added weighted scoring/)).toBeInTheDocument());
  // the changed metadata field is shown…
  expect(screen.getByText("title")).toBeInTheDocument();
  expect(screen.getByText("Supplier Selection & Evaluation")).toBeInTheDocument();
  // …and the unchanged "classification" field is NOT rendered as a change row.
  expect(screen.queryByText("classification")).not.toBeInTheDocument();
});

test("RedlineViewer renders ins/del with non-color +/- markers + semantics (DP-7)", async () => {
  renderWithProviders(<RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  const del = await screen.findByLabelText(/^Removed:/);
  expect(del.tagName.toLowerCase()).toBe("del");
  expect(del.textContent?.trimStart().startsWith("−")).toBe(true);
  const inserts = screen.getAllByLabelText(/^Added:/);
  expect(inserts).toHaveLength(2);
  expect(inserts[0]?.tagName.toLowerCase()).toBe("ins");
  expect(inserts[0]?.textContent?.trimStart().startsWith("+")).toBe(true);
});

test("RedlineViewer n/p keyboard navigation moves focus through the changes", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  const region = await screen.findByRole("group", { name: /Text redline/ });
  region.focus();
  await user.keyboard("n");
  expect(document.activeElement).toBe(screen.getByLabelText(/^Removed:/));
  await user.keyboard("n");
  expect(document.activeElement).toBe(screen.getAllByLabelText(/^Added:/)[0]);
  await user.keyboard("p");
  expect(document.activeElement).toBe(screen.getByLabelText(/^Removed:/));
});

test("RedlineViewer degrades to a source-download fallback when text is unavailable", async () => {
  const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
  server.use(
    http.get("/api/v1/documents/:id/versions/:vid/diff", () =>
      HttpResponse.json({
        ...diffFixture,
        text_diff: { status: "unavailable", reason: "Tika is unavailable." },
      }),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() => expect(screen.getByText("Text redline unavailable")).toBeInTheDocument());
  expect(screen.getByText("Tika is unavailable.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Download Rev A source/ }));
  await waitFor(() => expect(openSpy).toHaveBeenCalled());
});

test("RedlineViewer shows quiet no-access on a 403 (document.read_draft)", async () => {
  server.use(
    http.get("/api/v1/documents/:id/versions/:vid/diff", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />);
  await waitFor(() =>
    expect(screen.getByText("You don't have access to the redline.")).toBeInTheDocument(),
  );
});

test("RedlineViewer has no a11y violations", async () => {
  const { container } = renderWithProviders(
    <RedlineViewer documentId={DOC} fromVid={FROM} toVid={TO} />,
  );
  await screen.findByText(/Added weighted scoring/);
  expect(await axe(container)).toHaveNoViolations();
});
