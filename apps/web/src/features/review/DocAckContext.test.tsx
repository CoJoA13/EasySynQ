import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DocAckContext } from "./DocAckContext";

const DOC = "11111111-1111-1111-1111-111111111111";

describe("DocAckContext", () => {
  test("shows the document identifier + title", async () => {
    renderWithProviders(<DocAckContext documentId={DOC} />);
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("Supplier Selection & Evaluation")).toBeInTheDocument();
  });

  test("a 403 degrades calmly (the card still renders elsewhere)", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })),
    );
    renderWithProviders(<DocAckContext documentId={DOC} />);
    expect(await screen.findByText(/Document details aren't visible to you/i)).toBeInTheDocument();
  });
});
