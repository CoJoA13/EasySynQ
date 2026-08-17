import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { FirstAdministratorStep } from "./FirstAdministratorStep";

const ADMIN_ID = "ad000001-0001-0001-0001-000000000001";
const OLD_PASSWORD = "Old-Response-Lost-Password-7";
const TEMPORARY_PASSWORD = "New-Only-Temporary-Password-8";
const REISSUED_PASSWORD = "Reissued-Current-Temporary-Password-9";
const SETUP_SECRET = "setup-secret-visible-only-in-memory";
const MAXIMUM_FIRST_ADMIN_SECRET = "S".repeat(512);
const CURRENT_SETUP_SECRET = "current-reminted-setup-secret";
const CREDENTIAL_RECEIPT = "R".repeat(43);
const REISSUED_CREDENTIAL_RECEIPT = "N".repeat(43);
const LOCAL_STORAGE_DESCRIPTOR = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

const PROVISIONED = {
  administrator: {
    id: ADMIN_ID,
    username: "first.admin",
    display_name: "First Administrator",
    email: null,
    status: "INVITED",
  },
  temporary_password: TEMPORARY_PASSWORD,
  credential_receipt: CREDENTIAL_RECEIPT,
  password_delivery: "shown_once",
} as const;

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: Deferred<T>["resolve"];
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function storageEntries(storage: Storage | undefined): string[] {
  if (storage === undefined) return [];
  const values: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) values.push(`${key}=${storage.getItem(key) ?? ""}`);
  }
  return values;
}

function serializeClientCache(queryClient: QueryClient): string {
  return JSON.stringify(
    {
      queries: queryClient
        .getQueryCache()
        .getAll()
        .map((query) => ({
          queryKey: query.queryKey,
          data: query.state.data,
          error: query.state.error,
          fetchFailureReason: query.state.fetchFailureReason,
          state: query.state,
        })),
      mutations: queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => ({
          mutationKey: mutation.options.mutationKey,
          data: mutation.state.data,
          variables: mutation.state.variables,
          context: mutation.state.context,
          error: mutation.state.error,
          failureReason: mutation.state.failureReason,
          state: mutation.state,
        })),
    },
    (_key, value: unknown) =>
      value instanceof Error
        ? {
            name: value.name,
            message: value.message,
            cause: value.cause,
            ...Object.fromEntries(Object.entries(value)),
          }
        : value,
  );
}

function observeClientCache(queryClient: QueryClient) {
  const snapshots = [serializeClientCache(queryClient)];
  const capture = () => snapshots.push(serializeClientCache(queryClient));
  const unsubscribeQuery = queryClient.getQueryCache().subscribe(capture);
  const unsubscribeMutation = queryClient.getMutationCache().subscribe(capture);
  return {
    snapshots,
    stop: () => {
      capture();
      unsubscribeQuery();
      unsubscribeMutation();
    },
  };
}

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => data.delete(key),
    setItem: (key, value) => data.set(key, value),
  };
}

async function fillRequiredForm(
  user: ReturnType<typeof userEvent.setup>,
  values: { secret?: string; username?: string; displayName?: string } = {},
): Promise<void> {
  await user.type(screen.getByLabelText(/^Setup secret/), values.secret ?? SETUP_SECRET);
  await user.type(screen.getByLabelText(/^Username/), values.username ?? "first.admin");
  await user.type(
    screen.getByLabelText(/^Display name/),
    values.displayName ?? "First Administrator",
  );
}

function renderStep(
  onAcknowledged: () => Promise<void> = vi.fn(async () => undefined),
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return {
    ...renderWithProviders(<FirstAdministratorStep onAcknowledged={onAcknowledged} />, {
      queryClient,
    }),
    queryClient,
  };
}

beforeAll(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: memoryStorage(),
  });
});

afterAll(() => {
  if (LOCAL_STORAGE_DESCRIPTOR) {
    Object.defineProperty(globalThis, "localStorage", LOCAL_STORAGE_DESCRIPTOR);
  } else {
    Reflect.deleteProperty(globalThis, "localStorage");
  }
});

afterEach(() => {
  vi.restoreAllMocks();
  globalThis.localStorage?.clear();
  globalThis.sessionStorage?.clear();
});

test("renders the native first-administrator profile form and requires nonblank identity fields", async () => {
  const user = userEvent.setup();
  const view = renderStep();

  expect(
    screen.getByRole("heading", { name: "Create the first administrator" }),
  ).toBeInTheDocument();
  for (const label of [/^Setup secret/, /^Username/, /^Display name/]) {
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  }
  for (const label of ["Email", "First name", "Last name"]) {
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  }
  const submit = screen.getByRole("button", { name: "Create administrator" });
  expect(submit).toBeDisabled();
  await user.type(screen.getByLabelText(/^Setup secret/), "   ");
  await user.type(screen.getByLabelText(/^Username/), "   ");
  await user.type(screen.getByLabelText(/^Display name/), "   ");
  expect(submit).toBeDisabled();
  expect(await axe(view.container)).toHaveNoViolations();
});

test("provisions without a bearer, normalizes optional blanks, and keeps both secrets volatile", async () => {
  let requestBody: unknown;
  let authorization: string | null = "not-captured";
  server.use(
    http.post("/api/v1/setup/administrator", async ({ request }) => {
      requestBody = await request.json();
      authorization = request.headers.get("authorization");
      return HttpResponse.json(PROVISIONED, { status: 201 });
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const localSetItem = vi.spyOn(globalThis.localStorage, "setItem");
  const sessionSetItem = vi.spyOn(globalThis.sessionStorage, "setItem");
  const cacheObservation = observeClientCache(queryClient);
  const user = userEvent.setup();
  const view = renderStep(undefined, queryClient);

  await fillRequiredForm(user, {
    secret: `  ${SETUP_SECRET}  `,
    username: "  first.admin  ",
    displayName: "  First Administrator  ",
  });
  await user.type(screen.getByLabelText("Email"), "   ");
  await user.type(screen.getByLabelText("First name"), "   ");
  await user.type(screen.getByLabelText("Last name"), "   ");
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  const heading = await screen.findByRole("heading", {
    name: "Temporary password — shown once",
  });
  expect(heading).toHaveFocus();
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Create the first administrator" })).toBeNull();
  expect(authorization).toBeNull();
  expect(requestBody).toEqual({
    secret: SETUP_SECRET,
    username: "first.admin",
    display_name: "First Administrator",
    email: null,
    first_name: null,
    last_name: null,
  });

  cacheObservation.stop();
  for (const secret of [SETUP_SECRET, TEMPORARY_PASSWORD, CREDENTIAL_RECEIPT]) {
    expect(window.location.href).not.toContain(secret);
    expect(storageEntries(globalThis.localStorage).join(" ")).not.toContain(secret);
    expect(storageEntries(globalThis.sessionStorage).join(" ")).not.toContain(secret);
    expect(cacheObservation.snapshots.join(" ")).not.toContain(secret);
  }
  expect(localSetItem).not.toHaveBeenCalled();
  expect(sessionSetItem).not.toHaveBeenCalled();
  expect(await axe(view.container)).toHaveNoViolations();
});

test("Copy reads the in-memory password", async () => {
  server.use(
    http.post("/api/v1/setup/administrator", () => HttpResponse.json(PROVISIONED, { status: 201 })),
  );
  const user = userEvent.setup();
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  renderStep();
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  await user.click(await screen.findByRole("button", { name: "Copy temporary password" }));

  expect(writeText).toHaveBeenCalledTimes(1);
  expect(writeText).toHaveBeenCalledWith(TEMPORARY_PASSWORD);
});

test("acknowledgment posts the current secret and receipt, remains visible while pending, and clears before one callback", async () => {
  const ackResponse = deferred<Response>();
  let ackBody: unknown;
  let ackAuthorization: string | null = "not-captured";
  let ackCalls = 0;
  server.use(
    http.post("/api/v1/setup/administrator", () => HttpResponse.json(PROVISIONED, { status: 201 })),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      ackCalls += 1;
      ackBody = await request.json();
      ackAuthorization = request.headers.get("authorization");
      return ackResponse.promise;
    }),
  );
  const onAcknowledged = vi.fn(async () => {
    expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
    for (const label of [
      /^Setup secret/,
      /^Username/,
      /^Display name/,
      "Email",
      "First name",
      "Last name",
    ]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    expect(screen.queryByRole("alert", { name: "Password receipt was not saved" })).toBeNull();
    expect(screen.queryByRole("alert", { name: "Current setup secret required" })).toBeNull();
    expect(
      screen.queryByRole("alert", { name: "Temporary password no longer current" }),
    ).toBeNull();
    expect(
      screen.queryByRole("alert", { name: "New temporary password was not issued" }),
    ).toBeNull();
    expect(
      screen.queryByRole("alert", { name: "Current setup secret required for reissue" }),
    ).toBeNull();
    expect(document.body).not.toHaveTextContent(MAXIMUM_FIRST_ADMIN_SECRET);
    expect(document.body).not.toHaveTextContent(CREDENTIAL_RECEIPT);
    expect(storageEntries(globalThis.localStorage).join(" ")).not.toContain(CREDENTIAL_RECEIPT);
    expect(storageEntries(globalThis.sessionStorage).join(" ")).not.toContain(CREDENTIAL_RECEIPT);
    expect(serializeClientCache(queryClient)).not.toContain(CREDENTIAL_RECEIPT);
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const localSetItem = vi.spyOn(globalThis.localStorage, "setItem");
  const sessionSetItem = vi.spyOn(globalThis.sessionStorage, "setItem");
  const cacheObservation = observeClientCache(queryClient);
  const user = userEvent.setup();
  renderStep(onAcknowledged, queryClient);
  await fillRequiredForm(user, { secret: MAXIMUM_FIRST_ADMIN_SECRET });
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  const continueButton = await screen.findByRole("button", {
    name: "I’ve saved it — Continue to sign in",
  });
  await user.click(continueButton);
  await waitFor(() => expect(ackCalls).toBe(1));
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeDisabled();
  expect(continueButton).toBeDisabled();
  expect(screen.getByRole("alert", { name: "Saving password receipt" })).toBeInTheDocument();
  await user.click(continueButton);
  expect(ackCalls).toBe(1);

  await act(async () => {
    ackResponse.resolve(
      HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID }, { status: 200 }),
    );
  });

  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(ackBody).toEqual({
    secret: MAXIMUM_FIRST_ADMIN_SECRET,
    credential_receipt: CREDENTIAL_RECEIPT,
  });
  expect(ackAuthorization).toBeNull();
  expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
  cacheObservation.stop();
  for (const secret of [MAXIMUM_FIRST_ADMIN_SECRET, TEMPORARY_PASSWORD, CREDENTIAL_RECEIPT]) {
    expect(storageEntries(globalThis.localStorage).join(" ")).not.toContain(secret);
    expect(storageEntries(globalThis.sessionStorage).join(" ")).not.toContain(secret);
    expect(cacheObservation.snapshots.join(" ")).not.toContain(secret);
  }
  expect(localSetItem).not.toHaveBeenCalled();
  expect(sessionSetItem).not.toHaveBeenCalled();
});

test("bootstrap_invalid keeps the password and retries the same receipt with a focused current-secret proof", async () => {
  const retryResponse = deferred<Response>();
  const acknowledgeBodies: unknown[] = [];
  let provisionCalls = 0;
  server.use(
    http.post("/api/v1/setup/administrator", () => {
      provisionCalls += 1;
      return HttpResponse.json(PROVISIONED, { status: 201 });
    }),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      acknowledgeBodies.push(await request.json());
      if (acknowledgeBodies.length === 1) {
        return HttpResponse.json(
          { code: "bootstrap_invalid", title: "unsafe invalid detail" },
          { status: 403 },
        );
      }
      return retryResponse.promise;
    }),
  );
  const onAcknowledged = vi.fn(async () => {
    expect(screen.queryByLabelText(/^Current setup secret/)).toBeNull();
    expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
  });
  const user = userEvent.setup();
  const view = renderStep(onAcknowledged);
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await user.click(
    await screen.findByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );

  expect(await screen.findByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByText("Enter current setup secret")).toBeInTheDocument();
  const currentSecret = screen
    .getAllByLabelText(/^Current setup secret/)
    .find((element): element is HTMLInputElement => element instanceof HTMLInputElement);
  expect(currentSecret).toBeDefined();
  if (currentSecret === undefined) throw new Error("Current setup secret input was not rendered");
  await waitFor(() => expect(currentSecret).toHaveFocus());
  expect(currentSecret).toBeRequired();
  expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Retry with current setup secret" }),
  ).toBeInTheDocument();
  expect(provisionCalls).toBe(1);

  await user.type(currentSecret, CURRENT_SETUP_SECRET);
  await user.click(screen.getByRole("button", { name: "Retry with current setup secret" }));
  await waitFor(() => expect(acknowledgeBodies).toHaveLength(2));
  const duringReplacementAcknowledgment = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(duringReplacementAcknowledgment);
  expect(duringReplacementAcknowledgment.defaultPrevented).toBe(true);
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Retry with current setup secret" }),
  ).toBeDisabled();
  expect(provisionCalls).toBe(1);

  await act(async () => {
    retryResponse.resolve(
      HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID }, { status: 200 }),
    );
  });

  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(acknowledgeBodies).toEqual([
    { secret: SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
    { secret: CURRENT_SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
  ]);
  expect(provisionCalls).toBe(1);
  expect(await axe(view.container)).toHaveNoViolations();
});

test("bootstrap_credential_superseded reissues the bound normalized profile and atomically replaces the password receipt", async () => {
  const reissueResponse = deferred<Response>();
  const provisionBodies: unknown[] = [];
  const acknowledgeBodies: unknown[] = [];
  server.use(
    http.post("/api/v1/setup/administrator", async ({ request }) => {
      provisionBodies.push(await request.json());
      if (provisionBodies.length === 1) {
        return HttpResponse.json(PROVISIONED, { status: 201 });
      }
      return reissueResponse.promise;
    }),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      acknowledgeBodies.push(await request.json());
      if (acknowledgeBodies.length === 1) {
        return HttpResponse.json(
          { code: "bootstrap_credential_superseded", title: "unsafe supersession detail" },
          { status: 409 },
        );
      }
      return HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID });
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const localSetItem = vi.spyOn(globalThis.localStorage, "setItem");
  const sessionSetItem = vi.spyOn(globalThis.sessionStorage, "setItem");
  const cacheObservation = observeClientCache(queryClient);
  const onAcknowledged = vi.fn(async () => {
    expect(screen.queryByText(REISSUED_PASSWORD)).toBeNull();
    expect(serializeClientCache(queryClient)).not.toContain(REISSUED_CREDENTIAL_RECEIPT);
  });
  const user = userEvent.setup();
  const view = renderStep(onAcknowledged, queryClient);
  await fillRequiredForm(user, {
    secret: `  ${CURRENT_SETUP_SECRET}  `,
    username: "  FIRST.ADMIN  ",
    displayName: "  First Administrator  ",
  });
  await user.type(screen.getByLabelText("Email"), "  first.admin@example.test  ");
  await user.type(screen.getByLabelText("First name"), "  First  ");
  await user.type(screen.getByLabelText("Last name"), "  Administrator  ");
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await user.click(
    await screen.findByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );

  expect(await screen.findByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(
    screen.getByRole("alert", { name: "Temporary password no longer current" }),
  ).toHaveTextContent("no longer current");
  expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeDisabled();
  const reissue = screen.getByRole("button", { name: "Issue a new temporary password" });
  expect(reissue).toHaveTextContent(/^Issue new password$/);
  expect(reissue).toHaveStyle({ minHeight: "44px", maxWidth: "100%" });
  await waitFor(() => expect(reissue).toHaveFocus());
  await user.click(reissue);
  await waitFor(() => expect(provisionBodies).toHaveLength(2));

  const duringReissue = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(duringReissue);
  expect(duringReissue.defaultPrevented).toBe(true);
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Issue a new temporary password" })).toBeDisabled();
  expect(provisionBodies[1]).toEqual({
    secret: CURRENT_SETUP_SECRET,
    username: "first.admin",
    display_name: "First Administrator",
    email: "first.admin@example.test",
    first_name: "First",
    last_name: "Administrator",
  });

  await act(async () => {
    reissueResponse.resolve(
      HttpResponse.json(
        {
          ...PROVISIONED,
          temporary_password: REISSUED_PASSWORD,
          credential_receipt: REISSUED_CREDENTIAL_RECEIPT,
        },
        { status: 200 },
      ),
    );
  });

  expect(await screen.findByText(REISSUED_PASSWORD)).toBeInTheDocument();
  expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
  expect(screen.queryByRole("alert", { name: "Temporary password no longer current" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "I’ve saved it — Continue to sign in" }));
  await waitFor(() => expect(acknowledgeBodies).toHaveLength(2));
  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(acknowledgeBodies).toEqual([
    { secret: CURRENT_SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
    { secret: CURRENT_SETUP_SECRET, credential_receipt: REISSUED_CREDENTIAL_RECEIPT },
  ]);
  cacheObservation.stop();
  for (const volatileValue of [
    CURRENT_SETUP_SECRET,
    TEMPORARY_PASSWORD,
    REISSUED_PASSWORD,
    CREDENTIAL_RECEIPT,
    REISSUED_CREDENTIAL_RECEIPT,
  ]) {
    expect(window.location.href).not.toContain(volatileValue);
    expect(storageEntries(globalThis.localStorage).join(" ")).not.toContain(volatileValue);
    expect(storageEntries(globalThis.sessionStorage).join(" ")).not.toContain(volatileValue);
    expect(cacheObservation.snapshots.join(" ")).not.toContain(volatileValue);
  }
  expect(localSetItem).not.toHaveBeenCalled();
  expect(sessionSetItem).not.toHaveBeenCalled();
  expect(await axe(view.container)).toHaveNoViolations();
});

test("a failed reissue names the response-loss error and retries the retained canonical profile", async () => {
  const reissueRetry = deferred<Response>();
  const provisionBodies: unknown[] = [];
  const acknowledgeBodies: unknown[] = [];
  server.use(
    http.post("/api/v1/setup/administrator", async ({ request }) => {
      provisionBodies.push(await request.json());
      if (provisionBodies.length === 1) {
        return HttpResponse.json(PROVISIONED, { status: 201 });
      }
      if (provisionBodies.length === 2) {
        return HttpResponse.json(
          { code: "keycloak_unavailable", title: "unsafe provider response loss" },
          { status: 502 },
        );
      }
      return reissueRetry.promise;
    }),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      acknowledgeBodies.push(await request.json());
      if (acknowledgeBodies.length === 1) {
        return HttpResponse.json(
          { code: "bootstrap_credential_superseded", title: "unsafe supersession detail" },
          { status: 409 },
        );
      }
      return HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID });
    }),
  );
  const onAcknowledged = vi.fn(async () => undefined);
  const user = userEvent.setup();
  const view = renderStep(onAcknowledged);
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await user.click(
    await screen.findByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );
  await user.click(await screen.findByRole("button", { name: "Issue a new temporary password" }));

  const reissueError = await screen.findByRole("alert", {
    name: "New temporary password was not issued",
  });
  expect(reissueError).toHaveTextContent(
    "EasySynQ could not issue a replacement password. The stale password remains unusable.",
  );
  expect(reissueError).not.toHaveTextContent("unsafe provider response loss");
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeDisabled();
  const retry = screen.getByRole("button", { name: "Retry issuing temporary password" });
  await waitFor(() => expect(retry).toHaveFocus());

  await user.click(retry);
  await waitFor(() => expect(provisionBodies).toHaveLength(3));
  const duringRetry = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(duringRetry);
  expect(duringRetry.defaultPrevented).toBe(true);
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(retry).toBeDisabled();

  await act(async () => {
    reissueRetry.resolve(
      HttpResponse.json(
        {
          ...PROVISIONED,
          temporary_password: REISSUED_PASSWORD,
          credential_receipt: REISSUED_CREDENTIAL_RECEIPT,
        },
        { status: 200 },
      ),
    );
  });

  expect(await screen.findByText(REISSUED_PASSWORD)).toBeInTheDocument();
  expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
  expect(screen.queryByRole("alert", { name: "New temporary password was not issued" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "I’ve saved it — Continue to sign in" }));
  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(provisionBodies).toEqual([
    {
      secret: SETUP_SECRET,
      username: "first.admin",
      display_name: "First Administrator",
      email: null,
      first_name: null,
      last_name: null,
    },
    {
      secret: SETUP_SECRET,
      username: "first.admin",
      display_name: "First Administrator",
      email: null,
      first_name: null,
      last_name: null,
    },
    {
      secret: SETUP_SECRET,
      username: "first.admin",
      display_name: "First Administrator",
      email: null,
      first_name: null,
      last_name: null,
    },
  ]);
  expect(acknowledgeBodies).toEqual([
    { secret: SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
    { secret: SETUP_SECRET, credential_receipt: REISSUED_CREDENTIAL_RECEIPT },
  ]);
  expect(await axe(view.container)).toHaveNoViolations();
});

test("bootstrap_invalid during reissue requires a focused current secret for the next provision only", async () => {
  const provisionBodies: unknown[] = [];
  const acknowledgeBodies: unknown[] = [];
  server.use(
    http.post("/api/v1/setup/administrator", async ({ request }) => {
      provisionBodies.push(await request.json());
      if (provisionBodies.length === 1) {
        return HttpResponse.json(PROVISIONED, { status: 201 });
      }
      if (provisionBodies.length === 2) {
        return HttpResponse.json(
          { code: "bootstrap_invalid", title: "unsafe rejected proof" },
          { status: 403 },
        );
      }
      return HttpResponse.json(
        {
          ...PROVISIONED,
          temporary_password: REISSUED_PASSWORD,
          credential_receipt: REISSUED_CREDENTIAL_RECEIPT,
        },
        { status: 200 },
      );
    }),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      acknowledgeBodies.push(await request.json());
      if (acknowledgeBodies.length === 1) {
        return HttpResponse.json(
          { code: "bootstrap_credential_superseded", title: "unsafe supersession detail" },
          { status: 409 },
        );
      }
      return HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID });
    }),
  );
  const onAcknowledged = vi.fn(async () => undefined);
  const user = userEvent.setup();
  const view = renderStep(onAcknowledged);
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await user.click(
    await screen.findByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );
  await user.click(await screen.findByRole("button", { name: "Issue a new temporary password" }));

  expect(
    await screen.findByRole("alert", { name: "Current setup secret required for reissue" }),
  ).toHaveTextContent("Enter the current setup secret to issue a replacement password.");
  expect(screen.getByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy temporary password" })).toBeDisabled();
  const currentSecret = screen
    .getAllByLabelText(/^Current setup secret/)
    .find((element): element is HTMLInputElement => element instanceof HTMLInputElement);
  expect(currentSecret).toBeDefined();
  if (currentSecret === undefined) {
    throw new Error("Current setup secret input for reissue was not rendered");
  }
  await waitFor(() => expect(currentSecret).toHaveFocus());
  expect(currentSecret).toBeRequired();
  expect(acknowledgeBodies).toHaveLength(1);

  await user.type(currentSecret, CURRENT_SETUP_SECRET);
  await user.click(screen.getByRole("button", { name: "Retry issuing with current setup secret" }));
  await screen.findByText(REISSUED_PASSWORD);
  expect(provisionBodies).toHaveLength(3);
  expect(provisionBodies[2]).toEqual({
    secret: CURRENT_SETUP_SECRET,
    username: "first.admin",
    display_name: "First Administrator",
    email: null,
    first_name: null,
    last_name: null,
  });
  expect(acknowledgeBodies).toHaveLength(1);

  await user.click(screen.getByRole("button", { name: "I’ve saved it — Continue to sign in" }));
  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(acknowledgeBodies).toEqual([
    { secret: SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
    { secret: CURRENT_SETUP_SECRET, credential_receipt: REISSUED_CREDENTIAL_RECEIPT },
  ]);
  expect(window.location.href).not.toContain(CURRENT_SETUP_SECRET);
  expect(storageEntries(globalThis.localStorage).join(" ")).not.toContain(CURRENT_SETUP_SECRET);
  expect(storageEntries(globalThis.sessionStorage).join(" ")).not.toContain(CURRENT_SETUP_SECRET);
  expect(await axe(view.container)).toHaveNoViolations();
});

test("acknowledgment failure keeps the credential panel and offers a single-flight Retry", async () => {
  let acknowledgments = 0;
  const acknowledgeBodies: unknown[] = [];
  server.use(
    http.post("/api/v1/setup/administrator", () => HttpResponse.json(PROVISIONED, { status: 201 })),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      acknowledgments += 1;
      acknowledgeBodies.push(await request.json());
      if (acknowledgments === 1) {
        return HttpResponse.json(
          { code: "keycloak_unavailable", title: "unsafe raw outage detail" },
          { status: 503 },
        );
      }
      return HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID });
    }),
  );
  const onAcknowledged = vi.fn(async () => undefined);
  const user = userEvent.setup();
  renderStep(onAcknowledged);
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await user.click(
    await screen.findByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );

  expect(await screen.findByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.getByRole("alert", { name: "Password receipt was not saved" })).toHaveTextContent(
    "EasySynQ could not save your receipt. The password remains visible; retry before signing in.",
  );
  expect(screen.getByRole("button", { name: "Retry acknowledgment" })).toBeInTheDocument();
  expect(onAcknowledged).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Retry acknowledgment" }));
  await waitFor(() => expect(onAcknowledged).toHaveBeenCalledTimes(1));
  expect(acknowledgments).toBe(2);
  expect(acknowledgeBodies).toEqual([
    { secret: SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
    { secret: SETUP_SECRET, credential_receipt: CREDENTIAL_RECEIPT },
  ]);
});

test("a valid-secret bound-identity response renders only the verified bound username", async () => {
  const boundUsername = "claimed.admin";
  const leakedSubject = "keycloak-subject-must-never-render";
  server.use(
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(
        {
          code: "bootstrap_identity_bound",
          title: "unsafe title",
          detail: `unsafe detail ${leakedSubject}`,
          bound_username: boundUsername,
          keycloak_subject: leakedSubject,
        },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  const view = renderStep();
  await fillRequiredForm(user, { username: "different.admin" });
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  const heading = await screen.findByRole("heading", {
    name: "Administrator identity is already bound",
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(screen.getByText(boundUsername)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(leakedSubject);
  expect(document.body).not.toHaveTextContent("unsafe title");
  expect(document.body).not.toHaveTextContent("unsafe detail");
  expect(await axe(view.container)).toHaveNoViolations();
});

test.each([
  [
    "bootstrap_expired",
    403,
    "The setup secret has expired. Remint it on the EasySynQ host, then try again.",
  ],
  ["validation_error", 422, "Check the administrator details and try again."],
  [
    "bootstrap_administrator_exists",
    409,
    "An existing System Administrator assignment blocks public setup. Run the documented host release-administrator-blocker recovery, then try again.",
  ],
  [
    "user_exists",
    409,
    "The bound username belongs to an unrelated identity. Changing the username here cannot recover this claim. Ask a host identity administrator to resolve the collision.",
  ],
  [
    "keycloak_email_exists",
    409,
    "That email belongs to another identity. Keep the bound username and enter another email.",
  ],
  [
    "keycloak_unavailable",
    502,
    "The identity service is unavailable. Restore Keycloak connectivity, then try again.",
  ],
] as const)(
  "maps %s to safe actionable copy without identity-provider details",
  async (code, status, message) => {
    const leakedSubject = "kc-sensitive-subject";
    server.use(
      http.post("/api/v1/setup/administrator", () =>
        HttpResponse.json(
          {
            code,
            title: `unsafe title ${leakedSubject}`,
            detail: `unsafe detail ${leakedSubject}`,
            keycloak_subject: leakedSubject,
          },
          { status },
        ),
      ),
    );
    const user = userEvent.setup();
    const view = renderStep();
    await fillRequiredForm(user);
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    const heading = await screen.findByRole("heading", { name: "Administrator was not created" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(`unsafe title ${leakedSubject}`);
    expect(document.body).not.toHaveTextContent(`unsafe detail ${leakedSubject}`);
    expect(document.body).not.toHaveTextContent(leakedSubject);
    if (code === "keycloak_unavailable") expect(await axe(view.container)).toHaveNoViolations();
  },
);

test("beforeunload guards provisioning, a visible password, and acknowledgment, then cleans up", async () => {
  const provisionResponse = deferred<Response>();
  const acknowledgeResponse = deferred<Response>();
  server.use(
    http.post("/api/v1/setup/administrator", () => provisionResponse.promise),
    http.post("/api/v1/setup/administrator/acknowledge", () => acknowledgeResponse.promise),
  );
  const user = userEvent.setup();
  renderStep();
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));

  const duringProvision = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(duringProvision);
  expect(duringProvision.defaultPrevented).toBe(true);

  await act(async () => {
    provisionResponse.resolve(HttpResponse.json(PROVISIONED, { status: 201 }));
  });
  await screen.findByText(TEMPORARY_PASSWORD);
  const whileVisible = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(whileVisible);
  expect(whileVisible.defaultPrevented).toBe(true);

  await user.click(screen.getByRole("button", { name: "I’ve saved it — Continue to sign in" }));
  const duringAcknowledgment = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(duringAcknowledgment);
  expect(duringAcknowledgment.defaultPrevented).toBe(true);

  await act(async () => {
    acknowledgeResponse.resolve(
      HttpResponse.json({ setup_state: "IN_SETUP", admin_user_id: ADMIN_ID }),
    );
  });
  await waitFor(() => expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull());
  const afterAcknowledgment = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(afterAcknowledgment);
  expect(afterAcknowledgment.defaultPrevented).toBe(false);
});

test("reload cannot redisplay an old password and a response-loss resubmission shows only the reset", async () => {
  let attempts = 0;
  server.use(
    http.post("/api/v1/setup/administrator", () => {
      attempts += 1;
      if (attempts === 1) {
        return HttpResponse.json(
          { code: "keycloak_unavailable", title: "Response was lost" },
          { status: 502 },
        );
      }
      return HttpResponse.json(PROVISIONED, { status: 200 });
    }),
  );
  const user = userEvent.setup();
  const first = renderStep();
  await fillRequiredForm(user);
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  expect(await screen.findByText(/identity service is unavailable/i)).toBeInTheDocument();
  expect(screen.queryByText(OLD_PASSWORD)).toBeNull();

  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  expect(await screen.findByText(TEMPORARY_PASSWORD)).toBeInTheDocument();
  expect(screen.queryByText(OLD_PASSWORD)).toBeNull();

  first.unmount();
  renderStep();
  expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
  expect(
    screen.getByRole("heading", { name: "Create the first administrator" }),
  ).toBeInTheDocument();
});

test("uses one shrinkable DOM at 320px with 44px action targets and forced-colors focus hooks", async () => {
  server.use(
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(
        {
          ...PROVISIONED,
          temporary_password: "P".repeat(512),
        },
        { status: 201 },
      ),
    ),
  );
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 320 });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: query === "(forced-colors: active)",
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
  const user = userEvent.setup();
  renderStep();
  await fillRequiredForm(user, {
    username: "u".repeat(255),
    displayName: "D".repeat(255),
  });
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
  await screen.findByText("P".repeat(512));

  expect(screen.getAllByRole("heading", { name: "Temporary password — shown once" })).toHaveLength(
    1,
  );
  for (const button of screen.getAllByRole("button")) {
    expect(button).toHaveStyle({ minHeight: "44px" });
  }
  const panel = screen.getByTestId("first-administrator-step");
  expect(panel).toHaveStyle({ minWidth: "0rem", width: "100%" });
  const copy = screen.getByRole("button", { name: "Copy temporary password" });
  copy.focus();
  expect(copy).toHaveFocus();
  expect(window.matchMedia("(forced-colors: active)").matches).toBe(true);
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
});
