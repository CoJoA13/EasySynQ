// Tiny fetch helper: attaches the bearer token and surfaces the RFC 9457 problem `code` on errors
// (so callers can branch on e.g. "bootstrap_invalid"). Used by the S8a setup wizard.

import { useMemo } from "react";
import { useAuth } from "./auth";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

interface Problem {
  code?: string;
  title?: string;
  detail?: string;
}

async function request<T>(
  method: string,
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let problem: Problem = {};
    try {
      problem = (await resp.json()) as Problem;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, problem.code ?? "error", problem.detail ?? problem.title ?? `HTTP ${resp.status}`);
  }
  if (resp.status === 204 || resp.headers.get("content-length") === "0") {
    return undefined as T; // 204 No Content (e.g. DELETE) — no JSON body to parse
  }
  return (await resp.json()) as T;
}

export const apiGet = <T>(path: string, token: string | null = null): Promise<T> =>
  request<T>("GET", path, token);

export const apiSend = <T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> => request<T>(method, path, token, body);

// Token-aware client: pulls the bearer token from AuthContext so callers/hooks never thread it.
export function useApi() {
  const { token } = useAuth();
  return useMemo(
    () => ({
      get: <T>(path: string): Promise<T> => apiGet<T>(path, token),
      send: <T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> =>
        apiSend<T>(method, path, token, body),
    }),
    [token],
  );
}
