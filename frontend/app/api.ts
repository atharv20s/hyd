export type User = { id: string; email: string; full_name: string; service_description: string; target_clients: string; account_role: "operator" | "client"; client_lead_id?: string };
export type AuthResponse = { access_token: string; refresh_token: string; user: User };
let refreshing: Promise<string> | null = null;

export function storeAuth(auth: AuthResponse) {
  localStorage.setItem("hydps2_token", auth.access_token);
  localStorage.setItem("hydps2_refresh", auth.refresh_token);
}

export async function api<T>(path: string, options: RequestInit = {}, authenticated = true, retried = false): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("hydps2_token") : null;
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...options, signal: options.signal ?? AbortSignal.timeout(60000), headers: {
      "Content-Type": "application/json", ...(authenticated && token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers,
    }});
  } catch {
    throw new Error("Connection interrupted. Please check the service status and try again.");
  }
  if (response.status === 401 && authenticated && !retried && localStorage.getItem("hydps2_refresh")) {
    if (!refreshing) refreshing = (async () => {
      const auth = await api<AuthResponse>("/auth/refresh", {method: "POST", body: JSON.stringify({ refresh_token: localStorage.getItem("hydps2_refresh") })}, false);
      storeAuth(auth); return auth.access_token;
    })().finally(() => { refreshing = null; });
    await refreshing;
    return api<T>(path, options, authenticated, true);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.map((item: {msg: string}) => item.msg).join(". ") : payload.detail;
    throw new Error(detail || (response.status === 401 ? "Your session expired. Please sign in again." : "The service is unavailable. Please retry shortly."));
  }
  return payload as T;
}
