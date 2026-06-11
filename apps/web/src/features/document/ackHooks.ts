import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { AckMatrixRow, DistributionPayload, DistributionUpdateBody } from "../../lib/types";

// S-ack-2: the doc-page ack reads + writes. The distribution GET is document.read (counts for any
// reader); the named matrix + the writes are document.distribute (the Acks tab gates them per-key).

export function useDistribution(documentId: string) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["distribution", documentId],
    queryFn: () => api.get<DistributionPayload>(`/api/v1/documents/${documentId}/distribution`),
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// The named matrix is document.distribute-gated → a 403 is the EXPECTED no-access outcome for a plain
// reader. retry:false + the forbidden flag (the drift/compliance pattern). `enabled` is the ack flag:
// the matrix is empty/meaningless when acknowledgement is not required.
export function useAcknowledgements(documentId: string, enabled: boolean) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["acknowledgements", documentId],
    queryFn: () => api.get<AckMatrixRow[]>(`/api/v1/documents/${documentId}/acknowledgements`),
    enabled,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useUpdateDistribution(documentId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DistributionUpdateBody) =>
      api.send<DistributionPayload>("POST", `/api/v1/documents/${documentId}/distribution`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["distribution", documentId] });
      void qc.invalidateQueries({ queryKey: ["acknowledgements", documentId] });
      void qc.invalidateQueries({ queryKey: ["document", documentId] });
    },
  });
}

export function useDeleteDistributionEntry(documentId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) =>
      api.send<void>("DELETE", `/api/v1/documents/${documentId}/distribution/${entryId}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["distribution", documentId] });
      void qc.invalidateQueries({ queryKey: ["acknowledgements", documentId] });
    },
  });
}
