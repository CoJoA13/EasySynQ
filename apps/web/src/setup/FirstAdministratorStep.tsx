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

type AcknowledgeRecovery = "retry" | "replacement-secret" | "superseded" | null;
type ReissueRecovery = "retry" | "replacement-secret" | null;
type RetainedAdministratorProfile = Omit<FirstAdministratorRequest, "secret">;

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
    if (error.code === "bootstrap_administrator_exists") {
      return {
        heading: "Administrator was not created",
        message:
          "An existing System Administrator assignment blocks public setup. Run the documented host release-administrator-blocker recovery, then try again.",
      };
    }
    if (error.code === "user_exists") {
      return {
        heading: "Administrator was not created",
        message:
          "The bound username belongs to an unrelated identity. Changing the username here cannot recover this claim. Ask a host identity administrator to resolve the collision.",
      };
    }
    if (error.code === "keycloak_email_exists") {
      return {
        heading: "Administrator was not created",
        message: "That email belongs to another identity. Keep the bound username and enter another email.",
      };
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
  const [pending, setPending] = useState<"provision" | "acknowledge" | "reissue" | null>(null);
  const [error, setError] = useState<PresentedError | null>(null);
  const [acknowledgeRecovery, setAcknowledgeRecovery] = useState<AcknowledgeRecovery>(null);
  const [reissueRecovery, setReissueRecovery] = useState<ReissueRecovery>(null);
  const inFlightRef = useRef(false);
  const secretRef = useRef("");
  const passwordRef = useRef("");
  const receiptRef = useRef("");
  const profileRef = useRef<RetainedAdministratorProfile | null>(null);
  const errorHeadingRef = useRef<HTMLHeadingElement>(null);
  const replacementSecretRef = useRef<HTMLInputElement>(null);
  const reissueSecretRef = useRef<HTMLInputElement>(null);
  const reissueButtonRef = useRef<HTMLButtonElement>(null);

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

  useEffect(() => {
    if (pending !== null) return;
    if (acknowledgeRecovery === "replacement-secret") {
      replacementSecretRef.current?.focus();
    } else if (acknowledgeRecovery === "superseded") {
      if (reissueRecovery === "replacement-secret") {
        reissueSecretRef.current?.focus();
      } else {
        reissueButtonRef.current?.focus();
      }
    }
  }, [acknowledgeRecovery, pending, reissueRecovery]);

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

  const provision = async (reissue = false): Promise<void> => {
    if (inFlightRef.current) return;
    let request: FirstAdministratorRequest;
    if (reissue) {
      const profile = profileRef.current;
      const secret = secretRef.current.trim();
      if (profile === null || secret === "") return;
      request = { secret, ...profile };
    } else {
      if (!canSubmit) return;
      request = {
        secret: form.secret.trim(),
        username: form.username.trim(),
        display_name: form.displayName.trim(),
        email: optional(form.email),
        first_name: optional(form.firstName),
        last_name: optional(form.lastName),
      };
    }
    inFlightRef.current = true;
    setPending(reissue ? "reissue" : "provision");
    setError(null);
    if (!reissue) {
      setAcknowledgeRecovery(null);
      setReissueRecovery(null);
    }
    secretRef.current = request.secret;
    try {
      const { administrator, temporary_password, credential_receipt } =
        await apiSend<FirstAdministratorProvisioned>(
          "POST",
          "/api/v1/setup/administrator",
          null,
          request,
        );
      flushSync(() => {
        passwordRef.current = temporary_password;
        receiptRef.current = credential_receipt;
        profileRef.current = {
          username: administrator.username,
          display_name: request.display_name,
          email: request.email,
          first_name: request.first_name,
          last_name: request.last_name,
        };
        setTemporaryPassword(temporary_password);
        setAcknowledgeRecovery(null);
        setReissueRecovery(null);
      });
    } catch (caught) {
      if (reissue) {
        if (caught instanceof ApiError && caught.code === "bootstrap_invalid") {
          flushSync(() => {
            secretRef.current = "";
            setForm((current) => ({ ...current, secret: "" }));
            setReissueRecovery("replacement-secret");
          });
        } else {
          setReissueRecovery("retry");
        }
      } else {
        setError(provisionError(caught));
      }
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  const acknowledge = async (): Promise<void> => {
    const secret = secretRef.current.trim();
    if (
      inFlightRef.current ||
      passwordRef.current === "" ||
      receiptRef.current === "" ||
      secret === ""
    ) {
      return;
    }
    inFlightRef.current = true;
    secretRef.current = secret;
    setPending("acknowledge");
    try {
      try {
        await apiSend<BootstrapAcknowledgeResponse>(
          "POST",
          "/api/v1/setup/administrator/acknowledge",
          null,
          { secret, credential_receipt: receiptRef.current },
        );
      } catch (caught) {
        if (caught instanceof ApiError && caught.code === "bootstrap_invalid") {
          flushSync(() => {
            secretRef.current = "";
            setForm((current) => ({ ...current, secret: "" }));
            setAcknowledgeRecovery("replacement-secret");
          });
        } else if (
          caught instanceof ApiError &&
          caught.code === "bootstrap_credential_superseded"
        ) {
          setAcknowledgeRecovery("superseded");
          setReissueRecovery(null);
        } else {
          setAcknowledgeRecovery("retry");
        }
        return;
      }
      flushSync(() => {
        passwordRef.current = "";
        receiptRef.current = "";
        secretRef.current = "";
        profileRef.current = null;
        setTemporaryPassword("");
        setForm(EMPTY_FORM);
        setError(null);
        setAcknowledgeRecovery(null);
        setReissueRecovery(null);
      });
      await onAcknowledged();
    } finally {
      inFlightRef.current = false;
      setPending(null);
    }
  };

  if (temporaryPassword !== "") {
    const passwordSuperseded = acknowledgeRecovery === "superseded";
    return (
      <Stack data-testid="first-administrator-step" miw={0} w="100%" gap="sm">
        <fieldset
          disabled={passwordSuperseded}
          style={{ border: 0, margin: 0, minWidth: 0, padding: 0, width: "100%" }}
        >
          <ShowOncePassword
            password={temporaryPassword}
            onDone={() => void acknowledge()}
            doneLabel={
              acknowledgeRecovery === "retry"
                ? "Retry acknowledgment"
                : acknowledgeRecovery === "replacement-secret"
                  ? "Retry with current setup secret"
                  : "I’ve saved it — Continue to sign in"
            }
            description={
              acknowledgeRecovery === "superseded"
                ? "This temporary password is no longer current. Keep it visible until EasySynQ issues and shows the replacement."
                : "Save this password now. Continuing records receipt and starts sign-in; Keycloak will require a replacement at first sign-in. If this response was lost, submitting the bound username again resets the password and invalidates the old value."
            }
            busy={pending === "acknowledge"}
          />
        </fieldset>
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
        {acknowledgeRecovery === "retry" && (
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
        {acknowledgeRecovery === "replacement-secret" && (
          <Alert
            color="red"
            role="alert"
            aria-live="assertive"
            aria-label="Current setup secret required"
          >
            <Stack gap="xs">
              <Title order={3} size="h4">
                Enter current setup secret
              </Title>
              <Text size="sm">
                The previous setup secret was not accepted. Enter the current secret to acknowledge
                this same temporary password.
              </Text>
              <PasswordInput
                ref={replacementSecretRef}
                label="Current setup secret"
                required
                autoComplete="off"
                value={form.secret}
                styles={INPUT_STYLES}
                visibilityToggleButtonProps={{ style: { minHeight: 44, minWidth: 44 } }}
                onChange={(event) => updateForm("secret", event.currentTarget.value)}
              />
            </Stack>
          </Alert>
        )}
        {acknowledgeRecovery === "superseded" && (
          <Alert
            color="red"
            role="alert"
            aria-live="assertive"
            aria-label="Temporary password no longer current"
          >
            <Stack gap="xs">
              <Title order={3} size="h4">
                Temporary password no longer current
              </Title>
              <Text size="sm">
                A newer credential generation replaced the shown password. Issue a new temporary
                password before continuing.
              </Text>
              {reissueRecovery !== "replacement-secret" && (
                <Group justify="flex-end" wrap="wrap">
                  <Button
                    ref={reissueButtonRef}
                    onClick={() => void provision(true)}
                    loading={pending === "reissue"}
                    disabled={pending !== null}
                    aria-busy={pending === "reissue" || undefined}
                    aria-label={
                      reissueRecovery === "retry" ? undefined : "Issue a new temporary password"
                    }
                    style={{ minHeight: 44, maxWidth: "100%" }}
                  >
                    {reissueRecovery === "retry"
                      ? "Retry issuing temporary password"
                      : "Issue new password"}
                  </Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}
        {acknowledgeRecovery === "superseded" && reissueRecovery === "retry" && (
          <Alert
            color="red"
            role="alert"
            aria-live="assertive"
            aria-label="New temporary password was not issued"
          >
            EasySynQ could not issue a replacement password. The stale password remains unusable.
            Retry when the identity service is available.
          </Alert>
        )}
        {acknowledgeRecovery === "superseded" && reissueRecovery === "replacement-secret" && (
          <Alert
            color="red"
            role="alert"
            aria-live="assertive"
            aria-label="Current setup secret required for reissue"
          >
            <Stack gap="xs">
              <Title order={3} size="h4">
                Enter current setup secret to issue a new password
              </Title>
              <Text size="sm">
                Enter the current setup secret to issue a replacement password. The stale password
                cannot be acknowledged.
              </Text>
              <PasswordInput
                ref={reissueSecretRef}
                label="Current setup secret"
                required
                autoComplete="off"
                value={form.secret}
                styles={INPUT_STYLES}
                visibilityToggleButtonProps={{ style: { minHeight: 44, minWidth: 44 } }}
                onChange={(event) => updateForm("secret", event.currentTarget.value)}
              />
              <Group justify="flex-end" wrap="wrap">
                <Button
                  ref={reissueButtonRef}
                  onClick={() => void provision(true)}
                  loading={pending === "reissue"}
                  disabled={pending !== null || form.secret.trim() === ""}
                  aria-busy={pending === "reissue" || undefined}
                  style={{ minHeight: 44 }}
                >
                  Retry issuing with current setup secret
                </Button>
              </Group>
            </Stack>
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
