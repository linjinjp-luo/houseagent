// Local API client. The frontend never touches SQLite or Playwright directly - everything goes through
// the FastAPI backend at /api/v1 with the per-launch session token.

export interface ApiErrorBody {
  error_code: string;
  message_key: string;
  correlation_id: string;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly code: string;
  readonly messageKey: string;
  readonly correlationId: string;
  readonly details: Record<string, unknown>;
  readonly status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error_code);
    this.status = status;
    this.code = body.error_code;
    this.messageKey = body.message_key;
    this.correlationId = body.correlation_id;
    this.details = body.details ?? {};
  }

  /** Server unreachable or a failure the user cannot fix by retrying the same action. */
  get unrecoverable(): boolean {
    return this.code === "DATABASE_ERROR" || this.code === "READ_ONLY";
  }
}

function sessionToken(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="houseagent-token"]');
  return meta?.content ?? "";
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`/api/v1${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  return url.pathname + url.search;
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-HouseAgent-Token": sessionToken(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, {
      error_code: "BACKEND_UNREACHABLE",
      message_key: "error.BACKEND_UNREACHABLE",
      correlation_id: "",
      details: {},
    });
  }
  if (!res.ok) {
    let payload: ApiErrorBody;
    try {
      payload = (await res.json()) as ApiErrorBody;
    } catch {
      payload = { error_code: "UNKNOWN_ERROR", message_key: "error.UNKNOWN_ERROR", correlation_id: "", details: {} };
    }
    if (!payload.error_code) {
      payload = { error_code: "UNKNOWN_ERROR", message_key: "error.UNKNOWN_ERROR", correlation_id: "", details: {} };
    }
    throw new ApiError(res.status, payload);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, query),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};
