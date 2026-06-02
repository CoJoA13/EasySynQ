// Tiny fetch helper: attaches the bearer token and surfaces the RFC 9457 problem `code` on errors
// (so callers can branch on e.g. "bootstrap_invalid"). Used by the S8a setup wizard.

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
  return (await resp.json()) as T;
}

export const apiGet = <T>(path: string, token: string | null = null): Promise<T> =>
  request<T>("GET", path, token);

export const apiSend = <T>(
  method: "POST" | "PATCH",
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> => request<T>(method, path, token, body);
