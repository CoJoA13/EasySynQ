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
const SETUP_SECRET = "setup-secret-visible-only-in-memory";
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

function storageValues(storage: Storage | undefined): string[] {
  if (storage === undefined) return [];
  const values: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) values.push(storage.getItem(key) ?? "");
  }
  return values;
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
  globalThis.localStorage?.clear();
  globalThis.sessionStorage?.clear();
});

test("renders the native first-administrator profile form and requires nonblank identity fields", async () => {
  const user = userEvent.setup();
  const view = renderStep();

  expect(screen.getByRole("heading", { name: "Create the first administrator" })).toBeInTheDocument();
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

  const retainedQueryData = queryClient
    .getQueryCache()
    .getAll()
    .map((query) => JSON.stringify(query.state.data ?? null));
  const retainedMutationData = queryClient
    .getMutationCache()
    .getAll()
    .map((mutation) => JSON.stringify(mutation.state.data ?? null));
  for (const secret of [SETUP_SECRET, TEMPORARY_PASSWORD]) {
    expect(window.location.href).not.toContain(secret);
    expect(storageValues(globalThis.localStorage).join(" ")).not.toContain(secret);
    expect(storageValues(globalThis.sessionStorage).join(" ")).not.toContain(secret);
    expect(retainedQueryData.join(" ")).not.toContain(secret);
    expect(retainedMutationData.join(" ")).not.toContain(secret);
  }
  expect(await axe(view.container)).toHaveNoViolations();
});

test("Copy reads the in-memory password", async () => {
  server.use(
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(PROVISIONED, { status: 201 }),
    ),
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

test("acknowledgment posts only the setup secret, remains visible while pending, and clears before one callback", async () => {
  const ackResponse = deferred<Response>();
  let ackBody: unknown;
  let ackAuthorization: string | null = "not-captured";
  let ackCalls = 0;
  server.use(
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(PROVISIONED, { status: 201 }),
    ),
    http.post("/api/v1/setup/administrator/acknowledge", async ({ request }) => {
      ackCalls += 1;
      ackBody = await request.json();
      ackAuthorization = request.headers.get("authorization");
      return ackResponse.promise;
    }),
  );
  const onAcknowledged = vi.fn(async () => {
    expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
    expect(document.body).not.toHaveTextContent(SETUP_SECRET);
  });
  const user = userEvent.setup();
  renderStep(onAcknowledged);
  await fillRequiredForm(user);
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
  expect(ackBody).toEqual({ secret: SETUP_SECRET });
  expect(ackAuthorization).toBeNull();
  expect(screen.queryByText(TEMPORARY_PASSWORD)).toBeNull();
});

test("acknowledgment failure keeps the credential panel and offers a single-flight Retry", async () => {
  let acknowledgments = 0;
  server.use(
    http.post("/api/v1/setup/administrator", () =>
      HttpResponse.json(PROVISIONED, { status: 201 }),
    ),
    http.post("/api/v1/setup/administrator/acknowledge", () => {
      acknowledgments += 1;
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

  const heading = await screen.findByRole("heading", { name: "Administrator identity is already bound" });
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
    "user_exists",
    409,
    "That username or email belongs to another identity. Use a different value and try again.",
  ],
  [
    "keycloak_unavailable",
    502,
    "The identity service is unavailable. Restore Keycloak connectivity, then try again.",
  ],
] as const)("maps %s to safe actionable copy without identity-provider details", async (code, status, message) => {
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
  expect(document.body).not.toHaveTextContent(leakedSubject);
  if (code === "keycloak_unavailable") expect(await axe(view.container)).toHaveNoViolations();
});

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

  await user.click(
    screen.getByRole("button", { name: "I’ve saved it — Continue to sign in" }),
  );
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
  expect(screen.getByRole("heading", { name: "Create the first administrator" })).toBeInTheDocument();
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

  expect(screen.getAllByRole("heading", { name: "Temporary password — shown once" })).toHaveLength(1);
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
