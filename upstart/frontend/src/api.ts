/** The one place that knows about the HTTP API. */

export type Item = {
  id: number;
  name: string;
  note: string | null;
  done: boolean;
  created_at: string;
};

export type ItemInput = { name: string; note: string | null; done: boolean };

/** What the last request did — the UI shows this instead of hiding failures. */
export type Call = { method: string; path: string; status: number; message: string };

export class ApiError extends Error {
  constructor(readonly call: Call) {
    super(call.message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<[T, Call]> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const payload = response.status === 204 ? null : await response.json();
  const call = { method, path, status: response.status, message: "" };

  if (!response.ok) throw new ApiError({ ...call, message: errorMessage(payload) });
  return [payload as T, call];
}

/** FastAPI returns `detail` as a string (404) or a list of field errors (422). */
function errorMessage(payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((issue: { loc?: unknown[]; msg?: string }) => {
        const field = issue.loc?.[issue.loc.length - 1];
        return field ? `${field}: ${issue.msg}` : issue.msg;
      })
      .join("; ");
  }
  return "Request failed";
}

export const api = {
  list: () => request<Item[]>("GET", "/items"),
  read: (id: number) => request<Item>("GET", `/items/${id}`),
  create: (input: ItemInput) => request<Item>("POST", "/items", input),
  replace: (id: number, input: ItemInput) => request<Item>("PUT", `/items/${id}`, input),
  patch: (id: number, changes: Partial<ItemInput>) =>
    request<Item>("PATCH", `/items/${id}`, changes),
  remove: (id: number) => request<null>("DELETE", `/items/${id}`),
};
