import {
  Alert,
  Button,
  Group,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ShowOncePassword } from "../admin/ShowOncePassword";
import { ApiError, apiSend } from "../lib/api";
import type {
  BootstrapAcknowledgeResponse,
  FirstAdministratorProvisioned,
  FirstAdministratorRequest,
} from "../lib/types";

interface FirstAdministratorStepProps {
  onAcknowledged: () => Promise<void>;
}

interface FirstAdministratorForm {
  secret: string;
  username: string;
  displayName: string;
  email: string;
  firstName: string;
  lastName: string;
}

interface PresentedError {
  heading: string;
  message: string;
  boundUsername?: string;
}

const EMPTY_FORM: FirstAdministratorForm = {
  secret: "",
  username: "",
  displayName: "",
  email: "",
  firstName: "",
  lastName: "",
};

const INPUT_STYLES = { input: { minHeight: 44 } } as const;

function optional(value: string): string | null {
  const normalized = value.trim();
  return normalized === "" ? null : normalized;
}

function provisionError(error: unknown): PresentedError {
  if (error instanceof ApiError) {
    if (error.code === "bootstrap_identity_bound") {
      const boundUsername = error.problem?.bound_username;
      if (typeof boundUsername === "string" && boundUsername.trim() !== "") {
        return {
          heading: "Administrator identity is already bound",
          message:
            "This installation already claimed an administrator. Retry with the bound username shown below.",
          boundUsername,
        };
      }
    }
    if (error.code === "bootstrap_expired") {
      return {
        heading: "Administrator was not created",
        message: "The setup secret has expired. Remint it on the EasySynQ host, then try again.",
      };
    }
    if (
      error.code === "bootstrap_invalid" ||
      error.code === "no_bootstrap_secret" ||
      error.code === "bootstrap_already_consumed"
    ) {
      return {
        heading: "Administrator was not created",
        message: "The setup secret was not accepted. Check the current secret and try again.",
      };
    }
    if (error.code === "validation_error") {
      return {
        heading: "Administrator was not created",
        message: "Check the administrator details and try again.",
      };
    }
    if (error.code === "user_exists" || error.code === "keycloak_email_exists") {
      return {
        heading: "Administrator was not created",
        message:
          "That username or email belongs to another identity. Use a different value and try again.",
      };
    }
    if (
      error.code === "keycloak_unavailable" ||
      error.code === "keycloak_not_configured" ||
      error.code === "dependency_unavailable"
    ) {
      return {
        heading: "Administrator was not created",
        message:
          "The identity service is unavailable. Restore Keycloak connectivity, then try again.",
      };
    }
    if (error.code === "rate_limited") {
      return {
        heading: "Administrator was not created",
        message: "Too many setup attempts were made. Wait briefly, then try again.",
      };
    }
    if (error.code === "bootstrap_not_ready" || error.code === "setup_already_complete") {
      return {
        heading: "Administrator was not created",
        message: "This installation is not accepting first-administrator setup.",
      };
    }
  }
  return {
    heading: "Administrator was not created",
    message: "EasySynQ could not create the administrator. Check the details and try again.",
  };
}

export function FirstAdministratorStep({ onAcknowledged }: FirstAdministratorStepProps) {
  const [form, setForm] = useState<FirstAdministratorForm>(EMPTY_FORM);
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [pending, setPending] = useState<"provision" | "acknowledge" | null>(null);
  const [error, setError] = useState<PresentedError | null>(null);
  const [acknowledgeFailed, setAcknowledgeFailed] = useState(false);
  const [transitionFailed, setTransitionFailed] = useState(false);
  const inFlightRef = useRef(false);
  const secretRef = useRef("");
  const passwordRef = useRef("");
  const errorHeadingRef = useRef<HTMLHeadingElement>(null);

  const guarded = pending !== null || temporaryPassword !== "";
  useEffect(() => {
    if (!guarded) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [guarded]);

  useEffect(() => {
    if (error !== null) errorHeadingRef.current?.focus();
  }, [error]);

  const updateForm = <K extends keyof FirstAdministratorForm>(
    field: K,
    value: FirstAdministratorForm[K],
  ) => {
    if (field === "secret") secretRef.current = value;
    setForm((current) => ({ ...current, [field]: value }));
  };

  const canSubmit =
    form.secret.trim() !== "" &&
    form.username.trim() !== "" &&
    form.displayName.trim() !== "";

  const provision = async (): Promise<void> => {
    if (inFlightRef.current || !canSubmit) return;
    inFlightRef.current = true;
    setPending("provision");
    setError(null);
    setAcknowledgeFailed(false);
    setTransitionFailed(false);
    const request: FirstAdministratorRequest = {
      secret: form.secret.trim(),
      username: form.username.trim(),
      display_name: form.displayName.trim(),
      email: optional(form.email),
      first_name: optional(form.firstName),
      last_name: optional(form.lastName),
    };
    secretRef.current = request.secret;
    try {
      const { temporary_password } = await apiSend<FirstAdministratorProvisioned>(
        "POST",
        "/api/v1/setup/administrator",
        null,
        request,
      );
      passwordRef.current = temporary_password;
      setTemporaryPassword(temporary_password);
    } catch (caught) {
      setError(provisionError(caught));
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  const acknowledge = async (): Promise<void> => {
    if (inFlightRef.current || passwordRef.current === "") return;
    inFlightRef.current = true;
    setPending("acknowledge");
    setAcknowledgeFailed(false);
    try {
      await apiSend<BootstrapAcknowledgeResponse>(
        "POST",
        "/api/v1/setup/administrator/acknowledge",
        null,
        { secret: secretRef.current },
      );
      flushSync(() => {
        passwordRef.current = "";
        secretRef.current = "";
        setTemporaryPassword("");
        setForm((current) => ({ ...current, secret: "" }));
      });
      await onAcknowledged();
    } catch {
      if (passwordRef.current !== "") setAcknowledgeFailed(true);
      else setTransitionFailed(true);
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  const retryTransition = async (): Promise<void> => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setPending("acknowledge");
    try {
      await onAcknowledged();
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  if (transitionFailed) {
    return (
      <Stack data-testid="first-administrator-step" miw={0} w="100%" gap="md">
        <Alert color="yellow" role="alert" aria-live="assertive">
          <Stack gap="sm">
            <Title order={2} size="h3">
              Password receipt was saved
            </Title>
            <Text size="sm">
              EasySynQ could not refresh setup status. Retry the status check; no password will be
              shown or issued again.
            </Text>
            <Button
              onClick={() => void retryTransition()}
              loading={pending !== null}
              disabled={pending !== null}
              style={{ minHeight: 44 }}
            >
              Retry setup status
            </Button>
          </Stack>
        </Alert>
      </Stack>
    );
  }

  if (temporaryPassword !== "") {
    return (
      <Stack data-testid="first-administrator-step" miw={0} w="100%" gap="sm">
        <ShowOncePassword
          password={temporaryPassword}
          onDone={() => void acknowledge()}
          doneLabel={
            acknowledgeFailed ? "Retry acknowledgment" : "I’ve saved it — Continue to sign in"
          }
          description="Save this password now. Continuing records receipt and starts sign-in; Keycloak will require a replacement at first sign-in. If this response was lost, submitting the bound username again resets the password and invalidates the old value."
          busy={pending === "acknowledge"}
        />
        {pending === "acknowledge" && (
          <Alert
            color="blue"
            role="status"
            aria-live="polite"
            aria-label="Saving password receipt"
          >
            Saving your receipt before sign-in…
          </Alert>
        )}
        {acknowledgeFailed && (
          <Alert
            color="red"
            role="alert"
            aria-live="assertive"
            aria-label="Password receipt was not saved"
          >
            EasySynQ could not save your receipt. The password remains visible; retry before
            signing in.
          </Alert>
        )}
      </Stack>
    );
  }

  return (
    <Stack data-testid="first-administrator-step" miw={0} w="100%" gap="md">
      <Stack gap={4}>
        <Title order={2}>Create the first administrator</Title>
        <Text size="sm" c="dimmed">
          Enter the one-time EasySynQ setup secret and the identity you will use to finish setup.
          You do not need a Keycloak console or subject identifier.
        </Text>
      </Stack>

      {error && (
        <Alert color={error.boundUsername ? "yellow" : "red"} role="alert" aria-live="assertive">
          <Stack gap="xs">
            <Title ref={errorHeadingRef} order={3} size="h4" tabIndex={-1}>
              {error.heading}
            </Title>
            <Text size="sm">{error.message}</Text>
            {error.boundUsername && (
              <Text size="sm">
                Bound username: <Text component="span" fw={700}>{error.boundUsername}</Text>
              </Text>
            )}
          </Stack>
        </Alert>
      )}

      <PasswordInput
        label="Setup secret"
        required
        autoComplete="off"
        value={form.secret}
        styles={INPUT_STYLES}
        onChange={(event) => updateForm("secret", event.currentTarget.value)}
      />
      <TextInput
        label="Username"
        required
        autoComplete="username"
        value={form.username}
        maxLength={255}
        styles={INPUT_STYLES}
        onChange={(event) => updateForm("username", event.currentTarget.value)}
      />
      <TextInput
        label="Display name"
        required
        autoComplete="name"
        value={form.displayName}
        maxLength={255}
        styles={INPUT_STYLES}
        onChange={(event) => updateForm("displayName", event.currentTarget.value)}
      />
      <TextInput
        label="Email"
        type="email"
        autoComplete="email"
        value={form.email}
        maxLength={320}
        styles={INPUT_STYLES}
        onChange={(event) => updateForm("email", event.currentTarget.value)}
      />
      <Group grow wrap="wrap" align="flex-start">
        <TextInput
          label="First name"
          autoComplete="given-name"
          value={form.firstName}
          maxLength={255}
          miw={0}
          styles={INPUT_STYLES}
          onChange={(event) => updateForm("firstName", event.currentTarget.value)}
        />
        <TextInput
          label="Last name"
          autoComplete="family-name"
          value={form.lastName}
          maxLength={255}
          miw={0}
          styles={INPUT_STYLES}
          onChange={(event) => updateForm("lastName", event.currentTarget.value)}
        />
      </Group>

      {pending === "provision" && (
        <Alert color="blue" role="status" aria-live="polite" aria-label="Creating administrator">
          EasySynQ is creating the identity and will keep this step open until the temporary
          password is shown.
        </Alert>
      )}

      <Group justify="flex-end" wrap="wrap">
        <Button
          onClick={() => void provision()}
          loading={pending === "provision"}
          disabled={!canSubmit || pending !== null}
          aria-busy={pending === "provision" || undefined}
          style={{ minHeight: 44 }}
        >
          Create administrator
        </Button>
      </Group>
    </Stack>
  );
}
