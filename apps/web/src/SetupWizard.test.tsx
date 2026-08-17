import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { SetupWizard } from "./SetupWizard";
import { renderWithProviders } from "./test/render";
import { server } from "./test/msw/server";

test("UNINITIALIZED renders public administrator creation without login or sensitive setup detail", async () => {
  const user = userEvent.setup();
  const login = vi.fn(async () => undefined);
  let detailReads = 0;
  server.use(
    http.get("/api/v1/setup", () => {
      detailReads += 1;
      return HttpResponse.json({});
    }),
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(
        {
          administrator: {
            id: "ad000001-0001-0001-0001-000000000001",
            username: "first.admin",
            display_name: "First Administrator",
            email: null,
            status: "INVITED",
          },
          temporary_password: "New-Only-Temporary-Password-8",
          password_delivery: "shown_once",
        },
        { status: 201 },
      ),
    ),
  );

  renderWithProviders(
    <SetupWizard
      setupState="UNINITIALIZED"
      token={null}
      login={login}
      onBootstrapAcknowledged={async () => undefined}
      onFinalized={async () => undefined}
    />,
  );

  expect(
    await screen.findByRole("heading", { name: "Create the first administrator" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  expect(login).not.toHaveBeenCalled();
  expect(detailReads).toBe(0);

  await user.type(screen.getByLabelText(/^Setup secret/), "one-time-setup-secret");
  await user.type(screen.getByLabelText(/^Username/), "first.admin");
  await user.type(screen.getByLabelText(/^Display name/), "First Administrator");
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  expect(await screen.findByText("New-Only-Temporary-Password-8")).toBeInTheDocument();
});

test("IN_SETUP without a token renders named sign-in recovery without the public form or setup detail", async () => {
  const user = userEvent.setup();
  const login = vi.fn(async () => undefined);
  let detailReads = 0;
  server.use(
    http.get("/api/v1/setup", () => {
      detailReads += 1;
      return HttpResponse.json({});
    }),
  );

  renderWithProviders(
    <SetupWizard
      setupState="IN_SETUP"
      token={null}
      login={login}
      onBootstrapAcknowledged={async () => undefined}
      onFinalized={async () => undefined}
    />,
  );

  expect(await screen.findByRole("heading", { name: "Sign in to continue setup" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Create the first administrator" })).toBeNull();
  expect(detailReads).toBe(0);
  expect(login).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Try sign-in again" }));
  expect(login).toHaveBeenCalledTimes(1);
});

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

  renderWithProviders(
    <SetupWizard
      setupState="IN_SETUP"
      token="test-token"
      login={() => {}}
      onBootstrapAcknowledged={async () => undefined}
      onFinalized={async () => undefined}
    />,
  );

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

const FINALIZATION_READY_DETAIL = {
  setup_state: "IN_SETUP",
  gates: {
    "G-A": true,
    "G-E": true,
    "G-B": true,
    "G-C": true,
    "G-D": true,
  },
  org_profile: {
    legal_name: "Example Quality Organization",
    short_code: "EXAMPLE",
    timezone: "America/Chicago",
  },
  backup: {
    configured: true,
    destination: "/var/lib/easysynq/backups",
    last_restore_test_at: "2026-08-09T12:00:00Z",
    last_restore_test_result: "PASS",
  },
  auth: {
    configured: true,
    method: "LOCAL",
    last_test_at: "2026-08-09T12:00:00Z",
  },
  tamper_evident: false,
};

test("finalize waits for onFinalized after a successful response", async () => {
  const user = userEvent.setup();
  const events: string[] = [];
  let finishFinalizeResponse: (() => void) | undefined;
  let releaseVerification: (() => void) | undefined;
  let finalizeCalls = 0;
  const onFinalized = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        events.push("callback");
        releaseVerification = resolve;
      }),
  );

  server.use(
    http.get("/api/v1/setup", () => HttpResponse.json(FINALIZATION_READY_DETAIL)),
    http.post("/api/v1/setup/finalize", () => {
      finalizeCalls += 1;
      return new Promise<Response>((resolve) => {
        finishFinalizeResponse = () => {
          events.push("finalize response");
          resolve(HttpResponse.json({}));
        };
      });
    }),
  );

  renderWithProviders(
    <SetupWizard
      setupState="IN_SETUP"
      token="test-token"
      login={() => {}}
      onBootstrapAcknowledged={async () => undefined}
      onFinalized={onFinalized}
    />,
  );

  const finalize = await screen.findByRole("button", { name: "Finalize setup" });
  await user.click(finalize);

  await waitFor(() => expect(finalizeCalls).toBe(1));
  expect(onFinalized).not.toHaveBeenCalled();
  expect(finalize).toBeDisabled();

  await act(async () => {
    finishFinalizeResponse?.();
  });

  await waitFor(() => expect(onFinalized).toHaveBeenCalledTimes(1));
  expect(events).toEqual(["finalize response", "callback"]);
  expect(finalize).toBeDisabled();

  await act(async () => {
    releaseVerification?.();
  });

  await waitFor(() => expect(finalize).not.toBeDisabled());
  expect(finalizeCalls).toBe(1);
  expect(onFinalized).toHaveBeenCalledTimes(1);
});

test("finalize failure keeps the wizard error and does not call onFinalized", async () => {
  const user = userEvent.setup();
  const onFinalized = vi.fn(async () => undefined);

  server.use(
    http.get("/api/v1/setup", () => HttpResponse.json(FINALIZATION_READY_DETAIL)),
    http.post("/api/v1/setup/finalize", () =>
      HttpResponse.json({ detail: "Finalization is still blocked" }, { status: 409 }),
    ),
  );

  renderWithProviders(
    <SetupWizard
      setupState="IN_SETUP"
      token="test-token"
      login={() => {}}
      onBootstrapAcknowledged={async () => undefined}
      onFinalized={onFinalized}
    />,
  );

  await user.click(await screen.findByRole("button", { name: "Finalize setup" }));

  expect(await screen.findByText("Finalization is still blocked")).toBeInTheDocument();
  expect(onFinalized).not.toHaveBeenCalled();
});
