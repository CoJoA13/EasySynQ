import { Alert, Button, Card, Checkbox, Group, Radio, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DecisionOutcome, DecisionSubjectType } from "../../lib/types";
import { useDecideTask } from "./hooks";

const NEEDS_COMMENT: DecisionOutcome[] = ["changes_requested", "reject"];

// Per-subject legal outcome sets. PERIODIC_REVIEW accepts ONLY complete | changes_requested
// (services/vault/review.py — approve/reject 422). DOCUMENT/CAPA stay byte-identical.
const OUTCOMES: Record<DecisionSubjectType, { value: DecisionOutcome; label: string }[]> = {
  DOCUMENT: [
    { value: "approve", label: "Approve" },
    { value: "changes_requested", label: "Request changes" },
    { value: "reject", label: "Reject" },
  ],
  CAPA: [
    { value: "approve", label: "Approve" },
    { value: "changes_requested", label: "Request changes" },
    { value: "reject", label: "Reject" },
  ],
  PERIODIC_REVIEW: [
    { value: "complete", label: "Confirm — no change needed" },
    { value: "changes_requested", label: "Changes needed — a revision is required" },
  ],
};
const SIGN_OUTCOME: Record<DecisionSubjectType, DecisionOutcome> = {
  DOCUMENT: "approve",
  CAPA: "approve",
  PERIODIC_REVIEW: "complete",
};
const SIGN_MEANING: Record<DecisionSubjectType, string> = {
  DOCUMENT: "approval",
  CAPA: "approval",
  PERIODIC_REVIEW: "review confirmed",
};

// S-web-5: the approver's decision form. Approve signs (a v1 logged confirmation, the signature_event
// is the audit record); request-changes/reject require a comment (the server 422s otherwise). SoD is
// enforced server-side — a 403 sod_violation is rendered calmly (the version author never reaches a
// decidable task, but the branch backstops an override-only edge case).
// S-web-8: PERIODIC_REVIEW variant — complete/changes_requested only; complete writes a
// review_confirmed signature server-side; 409 means the doc lost its Effective version mid-review.
export function DecisionCard({ taskId, subjectType, subjectId }: { taskId: string; subjectType: DecisionSubjectType; subjectId: string }) {
  const { user } = useAuth();
  const decide = useDecideTask();
  const navigate = useNavigate();
  const [outcome, setOutcome] = useState<DecisionOutcome | "">("");
  const [comment, setComment] = useState("");
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(() => crypto.randomUUID());

  const commentRequired = NEEDS_COMMENT.includes(outcome as DecisionOutcome);
  const commentMissing = commentRequired && comment.trim().length === 0;
  const needsSig = outcome === SIGN_OUTCOME[subjectType];
  const disabled = outcome === "" || commentMissing || (needsSig && !signed) || decide.isPending;
  const who = user?.profile?.name ?? user?.profile?.preferred_username ?? "you";

  async function submit() {
    setError(null);
    if (outcome === "") return;
    try {
      await decide.mutateAsync({
        taskId,
        subjectType,
        subjectId,
        idempotencyKey: idemKey,
        body: { outcome, comment: comment.trim() || undefined },
      });
      navigate("/tasks");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 403 && e.code === "sod_violation")
          setError("You can't approve this version (separation of duties).");
        else if (e.status === 409)
          setError(
            subjectType === "PERIODIC_REVIEW"
              ? "The document no longer has an Effective version to confirm — it may have been obsoleted or be under revision."
              : "This task was already decided.",
          );
        else if (e.status === 403 && e.code === "step_up_required")
          setError("Re-authentication is required to sign.");
        else setError(e.message);
      } else setError("Something went wrong. Please retry.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Decision</Text>
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Radio.Group
          value={outcome}
          onChange={(v) => setOutcome(v as DecisionOutcome)}
          label="Your decision"
          withAsterisk
        >
          <Stack gap="xs" mt="xs">
            {OUTCOMES[subjectType].map((o) => (
              <Radio key={o.value} value={o.value} label={o.label} />
            ))}
          </Stack>
        </Radio.Group>
        <Textarea
          label="Comment"
          value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
          required={commentRequired}
          withAsterisk={commentRequired}
          aria-describedby="decision-comment-rule"
          error={commentMissing ? "A comment is required to request changes or reject." : undefined}
        />
        <Text id="decision-comment-rule" size="xs" c="dimmed">
          Required when requesting changes or rejecting.
        </Text>
        {needsSig && (
          <Stack gap={4}>
            <Checkbox
              checked={signed}
              onChange={(e) => setSigned(e.currentTarget.checked)}
              label={`Signing as ${who} — meaning: ${SIGN_MEANING[subjectType]}`}
            />
            <Text size="xs" c="dimmed">
              v1 — single-factor logged confirmation.
            </Text>
          </Stack>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={decide.isPending} disabled={disabled}>
            Submit decision
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}
