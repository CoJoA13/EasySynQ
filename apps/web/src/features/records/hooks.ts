import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { RecordDetail, RecordListRequest, RecordPage } from "../../lib/types";

function buildRecordsQuery(request: RecordListRequest) {
  const query = new URLSearchParams();
  query.set("limit", String(request.limit));
  if (request.cursor !== undefined) query.set("cursor", request.cursor);
  if (request.q !== undefined) query.set("q", request.q);
  if (request.record_type !== undefined) query.set("record_type", request.record_type);
  if (request.source_document_id !== undefined)
    query.set("source_document_id", request.source_document_id);
  if (request.captured_by !== undefined) query.set("captured_by", request.captured_by);
  if (request.disposition_state !== undefined)
    query.set("disposition_state", request.disposition_state);
  if (request.legal_hold !== undefined) query.set("legal_hold", String(request.legal_hold));
  return query.toString();
}

export function useRecords(request: RecordListRequest) {
  const api = useApi();
  const query = buildRecordsQuery(request);
  return useQuery({
    queryKey: ["records", query],
    queryFn: ({ signal }) => api.get<RecordPage>(`/api/v1/records?${query}`, signal),
    retry: false,
  });
}

export function useRecord(recordId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["record", recordId],
    queryFn: ({ signal }) => api.get<RecordDetail>(`/api/v1/records/${recordId!}`, signal),
    enabled: recordId !== null,
    retry: false,
  });
}
