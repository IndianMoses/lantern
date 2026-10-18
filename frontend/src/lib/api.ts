export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

let csrfToken: string | null = null;
export function setCsrf(token: string | null) {
  csrfToken = token;
}
export function getCsrf(): string | null {
  return csrfToken;
}

/** POST that returns a file (blob) rather than JSON. */
export async function postBlob(url: string): Promise<Blob> {
  const res = await fetch(url, { method: "POST", headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {}, credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || res.statusText);
  return res.blob();
}

async function request<T>(method: string, url: string, body?: unknown, opts: { form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrfToken && method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  let payload: BodyInit | undefined;
  if (opts.form) payload = opts.form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, (data && (data as { detail?: unknown }).detail) || data || res.statusText);
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
  upload: <T>(url: string, form: FormData) => request<T>("POST", url, undefined, { form }),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (typeof e.detail === "string") return e.detail;
    if (Array.isArray(e.detail)) return (e.detail as { msg?: string }[]).map((d) => d.msg || JSON.stringify(d)).join("; ");
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
