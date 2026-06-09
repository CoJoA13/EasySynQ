// apps/web/src/features/capa/StageForms.tsx
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Radio,
  Stack,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import {
  useCapaActionPlan,
  useCapaClose,
  useCapaContainment,
  useCapaImplement,
  useCapaRootCause,
  useCapaVerify,
} from "./mutations";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

// A compact submit row + calm error/success line, shared by the stage forms.
function FormShell({
  error,
  done,
  doneLabel,
  children,
}: {
  error: string | null;
  done: boolean;
  doneLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      {done && (
        <Text size="sm" c="teal">
          {doneLabel}
        </Text>
      )}
      {children}
    </Stack>
  );
}

export function ContainmentForm({ capa }: { capa: Capa }) {
  const m = useCapaContainment(capa.id);
  const [correction, setCorrection] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { correction, evidence_note: note || undefined } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded.">
      <Textarea
        label="Correction taken"
        value={correction}
        onChange={(e) => setCorrection(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <TextInput label="Evidence note (optional)" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={correction.trim().length === 0}>
          Record correction
        </Button>
      </Group>
    </FormShell>
  );
}

export function RootCauseForm({ capa }: { capa: Capa }) {
  const m = useCapaRootCause(capa.id);
  const [rootCause, setRootCause] = useState("");
  const [method, setMethod] = useState("5-whys");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { root_cause: rootCause, method } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded.">
      <Textarea
        label="Root cause"
        value={rootCause}
        onChange={(e) => setRootCause(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <Radio.Group label="Method" value={method} onChange={setMethod}>
        <Group gap="md" mt={4}>
          <Radio value="5-whys" label="5-Whys" />
          <Radio value="fishbone" label="Fishbone" />
          <Radio value="other" label="Other" />
        </Group>
      </Radio.Group>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={rootCause.trim().length === 0}>
          Record root cause
        </Button>
      </Group>
    </FormShell>
  );
}

export function ActionPlanForm({ capa }: { capa: Capa }) {
  const m = useCapaActionPlan(capa.id);
  const [items, setItems] = useState<string[]>([""]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const valid = items.some((i) => i.trim().length > 0);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { action_items: items.filter((i) => i.trim().length > 0) } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Action plan proposed — awaiting approval.">
      <Text size="sm" fw={500}>
        Action items
      </Text>
      {items.map((it, i) => (
        <TextInput
          key={i}
          aria-label={`Action item ${i + 1}`}
          value={it}
          onChange={(e) =>
            setItems((prev) => prev.map((p, j) => (j === i ? e.currentTarget.value : p)))
          }
        />
      ))}
      <Group justify="space-between">
        <Button variant="subtle" size="xs" onClick={() => setItems((p) => [...p, ""])}>
          + Add item
        </Button>
        <Button onClick={() => void submit()} loading={m.isPending} disabled={!valid}>
          Propose action plan
        </Button>
      </Group>
    </FormShell>
  );
}

export function ImplementForm({ capa }: { capa: Capa }) {
  const m = useCapaImplement(capa.id);
  const [actionsDone, setActionsDone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { actions_done: actionsDone } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded — link completion evidence on the stage below.">
      <Textarea
        label="Actions completed"
        value={actionsDone}
        onChange={(e) => setActionsDone(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <Text size="xs" c="dimmed">
        After recording, link completion evidence to the new Implement stage (required to close).
      </Text>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={actionsDone.trim().length === 0}>
          Record implementation
        </Button>
      </Group>
    </FormShell>
  );
}

export function VerifyForm({ capa }: { capa: Capa }) {
  const { user } = useAuth();
  const m = useCapaVerify(capa.id);
  const [decision, setDecision] = useState<"effective" | "not_effective" | "">("");
  const [narrative, setNarrative] = useState("");
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const who = user?.profile?.name ?? user?.profile?.preferred_username ?? "you";
  const disabled = decision === "" || narrative.trim().length === 0 || !signed || m.isPending;
  async function submit() {
    setError(null);
    if (decision === "") return;
    try {
      await m.mutateAsync({ decision, content_block: { narrative } });
      setDone(true);
    } catch (e) {
      // SoD-4 (verifier ≠ implementer) is a server-only truth → surface 409 sod_self_verify calmly.
      if (e instanceof ApiError && e.status === 409 && e.code === "sod_self_verify")
        setError("You can't verify this CAPA — its action implementer may not verify it (SoD-4).");
      else setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Verification recorded.">
      <Radio.Group
        label="Effectiveness decision"
        value={decision}
        onChange={(v) => setDecision(v as "effective" | "not_effective")}
        withAsterisk
      >
        <Stack gap="xs" mt={4}>
          <Radio value="effective" label="Effective" />
          <Radio value="not_effective" label="Not effective (loops back to root cause)" />
        </Stack>
      </Radio.Group>
      <Textarea
        label="Verification narrative"
        value={narrative}
        onChange={(e) => setNarrative(e.currentTarget.value)}
        autosize
        minRows={2}
        withAsterisk
      />
      <Text size="xs" c="dimmed">
        Link effectiveness evidence to the new Verify stage below (required to close).
      </Text>
      <Checkbox
        checked={signed}
        onChange={(e) => setSigned(e.currentTarget.checked)}
        label={`Signing as ${who} — meaning: verify`}
      />
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={disabled}>
          Record verification
        </Button>
      </Group>
    </FormShell>
  );
}

// CloseAction does NOT gate the button on a client-derived readiness — the close gate is a SERVER-only
// truth (close_capa). The drawer renders the honest CloseGateStepper (Task 7) right above this panel, so
// the requirements are already visible; here we just submit and surface the server's 409 calmly (its
// message lists exactly what's missing). This avoids any client/server gate drift AND any dependency on
// deriveGate's shape/ordering.
export function CloseAction({ capa }: { capa: Capa }) {
  const m = useCapaClose(capa.id);
  const [error, setError] = useState<string | null>(null);
  const stages = capa.stages ?? [];
  const currentVerify = stages
    .filter((s) => s.stage === "Verify" && s.cycle_marker === capa.cycle_marker)
    .slice(-1)[0];
  const notEffective = currentVerify?.content_block?.decision === "not_effective";

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync();
    } catch (e) {
      // 409 capa_close_incomplete / capa_not_verified — the server's authoritative word (lists missing).
      if (e instanceof ApiError && e.status === 409) setError(e.message);
      else setError(errText(e));
    }
  }

  if (notEffective) {
    return (
      <Stack gap="xs">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm" c="dimmed">
          Verification was <b>not effective</b> — closing returns this CAPA to root cause for a revised plan.
        </Text>
        <Group justify="flex-end">
          <Button color="orange" onClick={() => void submit()} loading={m.isPending}>
            Return to root cause
          </Button>
        </Group>
      </Stack>
    );
  }
  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      <Text size="sm" c="dimmed">
        Closing requires root cause + a current-cycle action and effectiveness evidence (see the close gate
        above). The server confirms the gate and reports anything missing.
      </Text>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending}>
          Close CAPA
        </Button>
      </Group>
    </Stack>
  );
}
