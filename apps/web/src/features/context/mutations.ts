import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  ContextCreateBody,
  ContextIssue,
  ContextRegisterPublishBody,
  ContextRegisterStatus,
  ContextUpdateBody,
} from "../../lib/types";

// POST /context — create an issue (lazily mints the CTX head on the first one). Invalidate the list,
// the head status (it may have just been created), and the governing summary.
export function useCreateIssue() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ContextCreateBody) =>
      api.send<ContextIssue>("POST", "/api/v1/context", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["context"] });
      void qc.invalidateQueries({ queryKey: ["context-register"] });
      void qc.invalidateQueries({ queryKey: ["context-summary"] });
    },
  });
}

// PATCH /context/{id} — edit an issue (partial; omitted ≠ null, explicit null clears the nullable
// fields). Invalidate the single-issue cache + the list.
export function useUpdateIssue(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ContextUpdateBody) =>
      api.send<ContextIssue>("PATCH", `/api/v1/context/${id}`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["context", id] });
      void qc.invalidateQueries({ queryKey: ["context"] });
    },
  });
}

// The register-steward lifecycle (mirrors the risk console). Invalidate every read a lifecycle act
// changes: the head status (gates the edit affordances + the read-only banner AND carries the fresh
// can_release/can_manage caps), the working rows (re-frozen at publish), and the governing summary.
function useInvalidateContextRegister(): () => void {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["context-register"] });
    void qc.invalidateQueries({ queryKey: ["context"] });
    void qc.invalidateQueries({ queryKey: ["context-summary"] });
  };
}

// POST /context/register/start-revision — Effective→UnderRevision so rows become editable again
// (register.manage @ SYSTEM; 409 unless Effective).
export function useStartContextRegisterRevision() {
  const api = useApi();
  const invalidate = useInvalidateContextRegister();
  return useMutation({
    mutationFn: () =>
      api.send<ContextRegisterStatus>("POST", "/api/v1/context/register/start-revision"),
    onSuccess: () => invalidate(),
  });
}

// POST /context/register/publish — freeze the working rows into a new version and submit it for
// approval (register.manage @ SYSTEM; 409 unless Draft/UnderRevision; 409 on an empty register). The
// approve/decide step then rides the existing /tasks DOCUMENT arm — no FE here.
export function usePublishContextRegister() {
  const api = useApi();
  const invalidate = useInvalidateContextRegister();
  return useMutation({
    mutationFn: (body: ContextRegisterPublishBody) =>
      api.send<ContextRegisterStatus>("POST", "/api/v1/context/register/publish", body),
    onSuccess: () => invalidate(),
  });
}

// POST /context/register/release — promote the Approved version to Effective (document.release + SoD-2
// over the multi-axis release scope server-side). Empty body, matching the risk release.
export function useReleaseContextRegister() {
  const api = useApi();
  const invalidate = useInvalidateContextRegister();
  return useMutation({
    mutationFn: () =>
      api.send<ContextRegisterStatus>("POST", "/api/v1/context/register/release", {}),
    onSuccess: () => invalidate(),
  });
}
