"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Check, MapPin, SignOut, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { api, User } from "./api";
import { Brand } from "./brand";
import { Assistant } from "./assistant";
type Business = { business_name: string; category: string; address: string; phone: string; summary: string };
type Request = { _id: string; service: string; location: string; preferred_at: string; status: string };
export function ClientPortal({ user, logout }: { user: User; logout: () => void }) {
  const [business, setBusiness] = useState<Business | null>(null);
  const [requests, setRequests] = useState<Request[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    const [profile, bookings] = await Promise.all([api<Business>("/client/profile"), api<Request[]>("/client/requests")]); setBusiness(profile); setRequests(bookings);
  }, []);
  useEffect(() => { void load().catch(err => setError(err.message)); const timer = setInterval(() => { if (document.visibilityState === "visible") void load().catch(() => {}); }, 10000); return () => clearInterval(timer); }, [load]);
  async function request(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const fields = new FormData(form); setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<{ message: string }>("/client/intake", { method: "POST", body: JSON.stringify({ service: fields.get("service"), location: fields.get("location"), preferred_at: new Date(String(fields.get("time"))).toISOString(), contact: fields.get("contact") }) });
      setNotice(result.message); form.reset(); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not save request."); }
    finally { setBusy(false); }
  }
  return <main className="client-shell"><header className="client-header"><Brand /><button className="compact" onClick={logout}><SignOut />Sign out</button></header><section className="client-main"><p className="eyebrow">CLIENT-SIDE ASSISTANT · HYDERABAD</p><h1>{business?.business_name || "Your business assistant"}</h1><p className="muted"><MapPin />{business?.address || "Business details are loading…"}</p><p>Welcome, {user.full_name || "there"}. Ask a question, talk to the assistant, or request a service.</p>
    {(error || notice) && <div className={`alert ${error ? "error" : "success"}`} role="status">{error ? <WarningCircle /> : <Check />}{error || notice}</div>}
    <div className="client-grid"><Assistant client /><section className="panel"><div className="panel-head"><div><p className="eyebrow">WHERE & WHEN?</p><h2>Request a service.</h2></div></div><form className="panel-body strategy-form" onSubmit={request}><label>What do you need?<input name="service" required maxLength={1000} placeholder="e.g. Haircut and styling" /></label><label>Preferred location<input name="location" required maxLength={1000} defaultValue={business?.address || "Hyderabad"} /></label><label>Preferred date & time (your local time)<input name="time" type="datetime-local" required /></label><label>How can we reach you?<input name="contact" defaultValue={user.email} required maxLength={500} /></label><p className="compliance-note">This saves a request, not a confirmed booking. A human checks availability.</p><button className="primary" disabled={busy}>{busy ? <SpinnerGap className="spin" /> : <Check />}Send request</button></form></section></div>
    <section className="panel"><div className="panel-head"><h2>Your requests</h2></div><div className="request-list">{requests.length ? requests.map(item => <article key={item._id}><div><strong>{item.service}</strong><p>{item.location}</p><small>{new Date(item.preferred_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST</small></div><span className={`status status-${item.status}`}>{item.status.replaceAll("_", " ")}</span></article>) : <p className="muted">No service requests yet.</p>}</div></section><footer className="workspace-footer">Made for you. Made for <span lang="te">హైదరాబాద్</span>.</footer></section></main>;
}
