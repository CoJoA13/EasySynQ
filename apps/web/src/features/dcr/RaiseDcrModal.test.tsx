import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RaiseDcrModal } from "./RaiseDcrModal";

it("raises a CREATE DCR and calls onCreated with the new id", async () => {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(<RaiseDcrModal onClose={onClose} onCreated={onCreated} />);
  // CREATE needs no target
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "A new work instruction is needed.");
  await userEvent.click(screen.getByLabelText(/Reason class/));
  await userEvent.click(await screen.findByRole("option", { name: "Process improvement" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith("dcrNEW01-0001-0001-0001-000000000099"));
  expect(onClose).toHaveBeenCalled();
});

it("surfaces a 422 from the server calmly", async () => {
  server.use(
    http.post("/api/v1/dcrs", () =>
      HttpResponse.json(
        { code: "validation_error", title: "Invalid", detail: "A CREATE DCR must not target a document" },
        { status: 422 },
      ),
    ),
  );
  renderWithProviders(<RaiseDcrModal onClose={vi.fn()} onCreated={vi.fn()} />);
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText(/Reason for change/), "x");
  await userEvent.click(screen.getByLabelText(/Reason class/));
  await userEvent.click(await screen.findByRole("option", { name: "Other" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  expect(await screen.findByText("A CREATE DCR must not target a document")).toBeInTheDocument();
});
