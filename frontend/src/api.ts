export type UserPublic = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  operational_role?: string | null;
  branch_ids?: string[];
  permissions?: string[];
};

export type MeResponse = {
  user: UserPublic;
  csrf_token: string;
  /** Capability UI dari Master; otorisasi tetap di backend. */
  capabilities?: Record<string, boolean>;
};

function readCookie(name: string): string | null {
  const parts = document.cookie.split(";").map((part) => part.trim());
  for (const part of parts) {
    if (part.startsWith(`${name}=`)) {
      return decodeURIComponent(part.slice(name.length + 1));
    }
  }
  return null;
}

export function getCsrfToken(): string | null {
  return readCookie("eternal_csrf");
}

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string };
    if (typeof data.detail === "string") {
      return data.detail;
    }
  } catch {
    // ignore
  }
  return "Terjadi kesalahan. Silakan coba lagi.";
}

export async function fetchMe(): Promise<MeResponse | null> {
  const response = await fetch("/api/auth/me", {
    method: "GET",
    credentials: "include",
  });
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as MeResponse;
}

export async function login(email: string, password: string): Promise<MeResponse> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as MeResponse;
}

export async function logout(): Promise<void> {
  const csrf = getCsrfToken();
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: csrf ? { "X-CSRF-Token": csrf } : {},
  });
  if (!response.ok && response.status !== 401) {
    throw new Error(await parseError(response));
  }
}
