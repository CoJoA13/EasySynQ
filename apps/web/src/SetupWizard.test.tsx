import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { SetupWizard } from "./SetupWizard";
import { renderWithProviders } from "./test/render";
import { server } from "./test/msw/server";

test("the active backup gate describes integrity verification without claiming recovery", async () => {
  server.use(
    http.get("/api/v1/setup", () =>
      HttpResponse.json({
        setup_state: "IN_SETUP",
        gates: {
          "G-A": true,
          "G-E": true,
          "G-B": true,
          "G-C": false,
          "G-D": false,
        },
        org_profile: {
          legal_name: "Example Quality Organization",
          short_code: "EXAMPLE",
          timezone: "America/Chicago",
        },
        backup: {
          configured: true,
          destination: "/var/lib/easysynq/backups",
          last_restore_test_at: null,
          last_restore_test_result: null,
        },
        auth: {
          configured: false,
          method: null,
          last_test_at: null,
        },
        tamper_evident: false,
      }),
    ),
  );

  renderWithProviders(<SetupWizard token="test-token" login={() => {}} onFinalized={() => {}} />);

  expect(await screen.findByLabelText("Backup destination")).toBeInTheDocument();
  expect(
    screen.getByText(
      /Absolute non-root POSIX path\. Save checks only the API context; the worker drill checks current worker access, not persistent mount backing\./i,
    ),
  ).toBeInTheDocument();
  expect(screen.getByText(/currently configured source object store/i)).toBeInTheDocument();
  expect(
    screen.getByText(
      /A PASS is source-store-dependent integrity evidence only; it does not prove source-independent recovery or authorize cutover\./i,
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/prove a restore actually works/i)).not.toBeInTheDocument();
});
