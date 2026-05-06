// Tiny REST client. Reads the CSRF cookie and echoes it on mutating requests.

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

function readCsrf(): string {
  const match = document.cookie
    .split("; ")
    .find((c) => c.startsWith("fpk_csrf="));
  return match ? decodeURIComponent(match.split("=")[1]) : "";
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: { rawBody?: BodyInit }
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (body !== undefined && !init?.rawBody) {
    headers["Content-Type"] = "application/json";
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    const csrf = readCsrf();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  const resp = await fetch(path, {
    method,
    headers,
    body: init?.rawBody ?? (body !== undefined ? JSON.stringify(body) : undefined),
    credentials: "same-origin",
  });
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    parsed = text;
  }
  if (!resp.ok) {
    const detail =
      (parsed && typeof parsed === "object" && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : null) || text || resp.statusText;
    throw new ApiError(resp.status, detail);
  }
  return parsed as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, body?: unknown) => request<T>("POST", p, body),
  patch: <T>(p: string, body?: unknown) => request<T>("PATCH", p, body),
  del: <T>(p: string) => request<T>("DELETE", p),
  upload: <T>(p: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<T>("POST", p, undefined, { rawBody: fd });
  },
};

export type SetupStatus = { setup_complete: boolean };
export type Me = { username: string };

export type Account = {
  id: number;
  label: string;
  user_agent: string;
  note: string | null;
  proxy_present: boolean;
  funpay_user_id: number | null;
  funpay_username: string | null;
  last_checked_at: string | null;
  last_check_ok: boolean;
  last_check_error: string | null;
  enabled: boolean;
  created_at: string;
};

export type AccountCreate = {
  label: string;
  golden_key: string;
  proxy_url: string | null;
  user_agent: string;
  note: string | null;
};

export type ChatPreview = {
  id: string;
  title: string;
  last_message: string | null;
  unread: boolean;
  avatar_url: string | null;
};

export type ChatMessage = {
  id: string | null;
  author: string | null;
  is_me: boolean;
  text: string;
  sent_at: string | null;
};

export type ChatThread = {
  id: string;
  title: string;
  messages: ChatMessage[];
  peer_avatar_url: string | null;
};

export type AccountCheckResult = {
  ok: boolean;
  funpay_user_id: number | null;
  funpay_username: string | null;
  error: string | null;
};

export type PluginInfo = {
  slug: string;
  name: string;
  version: string;
  description: string;
  author: string | null;
  enabled: boolean;
  error: string | null;
  files: string[];
};

export type SendMessageRequest = { text: string };
