import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  InterestedParty,
  InterestedPartyCreateBody,
  InterestedPartyRegisterPublishBody,
  InterestedPartyRegisterStatus,
  InterestedPartyUpdateBody,
} from "../../lib/types";

// POST /interested-parties — create a party (lazily mints the IPR head on the first one). Invalidate
// the list, the head status (it may have just been created), and the governing summary.
export function useCreateParty() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InterestedPartyCreateBody) =>
      api.send<InterestedParty>("POST", "/api/v1/interested-parties", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["interested-parties"] });
      void qc.invalidateQueries({ queryKey: ["interested-parties-register"] });
      void qc.invalidateQueries({ queryKey: ["interested-parties-summary"] });
    },
  });
}

// PATCH /interested-parties/{id} — edit a party (partial; omitted ≠ null, explicit null clears the
// nullable fields). Invalidate the single-party cache + the list.
export function useUpdateParty(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InterestedPartyUpdateBody) =>
      api.send<InterestedParty>("PATCH", `/api/v1/interested-parties/${id}`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["interested-parties", id] });
      void qc.invalidateQueries({ queryKey: ["interested-parties"] });
    },
  });
}

// The register-steward lifecycle (mirrors the context/risk consoles). Invalidate every read a
// lifecycle act changes: the head status (gates the edit affordances + the read-only banner AND
// carries the fresh can_release/can_manage caps), the working rows (re-frozen at publish), and the
// governing summary.
function useInvalidateInterestedPartyRegister(): () => void {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["interested-parties-register"] });
    void qc.invalidateQueries({ queryKey: ["interested-parties"] });
    void qc.invalidateQueries({ queryKey: ["interested-parties-summary"] });
  };
}

// POST /interested-parties/register/start-revision — Effective→UnderRevision so rows become editable
// again (register.manage @ SYSTEM; 409 unless Effective).
export function useStartInterestedPartyRevision() {
  const api = useApi();
  const invalidate = useInvalidateInterestedPartyRegister();
  return useMutation({
    mutationFn: () =>
      api.send<InterestedPartyRegisterStatus>(
        "POST",
        "/api/v1/interested-parties/register/start-revision",
      ),
    onSuccess: () => invalidate(),
  });
}

// POST /interested-parties/register/publish — freeze the working rows into a new version and submit it
// for approval (register.manage @ SYSTEM; 409 unless Draft/UnderRevision; 409 on an empty register).
// The approve/decide step then rides the existing /tasks DOCUMENT arm — no FE here.
export function usePublishInterestedPartyRegister() {
  const api = useApi();
  const invalidate = useInvalidateInterestedPartyRegister();
  return useMutation({
    mutationFn: (body: InterestedPartyRegisterPublishBody) =>
      api.send<InterestedPartyRegisterStatus>(
        "POST",
        "/api/v1/interested-parties/register/publish",
        body,
      ),
    onSuccess: () => invalidate(),
  });
}

// POST /interested-parties/register/release — promote the Approved version to Effective
// (document.release + SoD-2 over the multi-axis release scope server-side). Empty body.
export function useReleaseInterestedPartyRegister() {
  const api = useApi();
  const invalidate = useInvalidateInterestedPartyRegister();
  return useMutation({
    mutationFn: () =>
      api.send<InterestedPartyRegisterStatus>(
        "POST",
        "/api/v1/interested-parties/register/release",
        {},
      ),
    onSuccess: () => invalidate(),
  });
}
