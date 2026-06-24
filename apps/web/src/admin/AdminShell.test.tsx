import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../test/render";
import { AdminShell } from "./AdminShell";

describe("AdminShell", () => {
  it("offers a Config tab", () => {
    renderWithProviders(<AdminShell />, { route: "/admin/config" });
    expect(screen.getByRole("tab", { name: "Config", selected: true })).toBeInTheDocument();
  });
});
