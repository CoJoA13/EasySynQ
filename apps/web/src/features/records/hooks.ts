import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";
import { useApi } from "../../lib/api";
import type { DocumentsPage, RecordDetail, RecordPage } from "../../lib/types";
import { buildRecordsQuery, type RecordUrlState } from "./recordUrlState";

export function useRecords(request: RecordUrlState & { limit: number }) {
  const api = useApi();
  const query = buildRecordsQuery(request);
  return useQuery({
    queryKey: ["records", query],
    queryFn: ({ signal }) => api.get<RecordPage>(`/api/v1/records?${query}`, { signal }),
    retry: false,
  });
}

export function useRecord(recordId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["record", recordId],
    queryFn: ({ signal }) => api.get<RecordDetail>(`/api/v1/records/${recordId!}`, { signal }),
    enabled: recordId !== null,
    refetchOnMount: "always",
    retry: false,
  });
}

export function useFreshRecordData(
  recordId: string | null,
  query: {
    data: RecordDetail | undefined;
    dataUpdatedAt: number;
    isSuccess: boolean;
  },
): RecordDetail | undefined {
  const initialDataUpdatedAt = useRef(query.dataUpdatedAt);
  if (
    !query.isSuccess ||
    query.dataUpdatedAt <= initialDataUpdatedAt.current ||
    query.data?.id !== recordId
  ) {
    return undefined;
  }
  return query.data;
}

export function useRecordSourceDocuments(q: string, enabled: boolean) {
  const api = useApi();
  const query = new URLSearchParams({ limit: "20", offset: "0" });
  if (q) query.set("q", q);
  const queryString = query.toString();
  return useQuery({
    queryKey: ["record-source-documents", queryString],
    queryFn: ({ signal }) => api.get<DocumentsPage>(`/api/v1/documents?${queryString}`, { signal }),
    enabled,
    retry: false,
  });
}
