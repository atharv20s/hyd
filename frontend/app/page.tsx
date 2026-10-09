"use client";
import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, Check, SpinnerGap, SignOut, WarningCircle } from "@phosphor-icons/react";
import { api, storeAuth, AuthResponse, User } from "./api";
import { Brand, Charminar, HyderabadLine } from "./brand";
import { Dashboard } from "./dashboard";
import { ClientPortal } from "./client-portal";

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [mode, setMode] = useState<"signup" | "login">("signup");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState("");
  useEffect(() => {
    setInvite(new URLSearchParams(location.search).get("invite") || "");
    if (!localStorage.getItem("hydps2_token")) { setLoading(false); return; }
    api<User>("/auth/me").then(setUser).catch(err => setError(err.message)).finally(() => setLoading(false));
  }, []);
  function logout() {
    localStorage.removeItem("hydps2_token"); localStorage.removeItem("hydps2_refresh"); setUser(null); setError("");
  }
  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<AuthResponse>(`/auth/${mode}`, { method: "POST", body: JSON.stringify({
        email: form.get("email"), password: form.get("password"), full_name: form.get("name") || "", invite_token: invite || undefined,
      }) }, false);
      storeAuth(result); setUser(result.user); history.replaceState({}, "", "/");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not sign in."); }
    finally { setBusy(false); }
  }
  if (loading) return <main className="loading-screen"><Brand /><SpinnerGap className="spin" size={30} /><p>Opening your workspace…</p></main>;
  if (user) return user.account_role === "client" ? <ClientPortal user={user} logout={logout} /> : <Dashboard user={user} setUser={setUser} logout={logout} />;
  return <main className="auth-shell">
    <section className="auth-story"><Brand /><div className="story-main"><p className="eyebrow">YOUR CITY. YOUR NEXT CHAPTER.</p><HyderabadLine />
      <p className="lede">Good businesses deserve to be found.<br />Meet your AI-powered growth desk, rooted in Hyderabad.</p>
      <div className="story-features"><span><Check /> Find the right businesses</span><span><Check /> Make every message personal</span><span><Check /> Keep the human in charge</span></div>
      <Charminar />
    </div><div className="story-footer"><span>17.3850° N · 78.4867° E</span><span>FROM CHARMINAR TO YOUR NEXT CLIENT</span></div></section>
    <section className="auth-panel"><div className="auth-top"><span>INTELLIGENCE, WITH A LOCAL TOUCH.</span><span>HYD / IN</span></div><div className="auth-card">
      <p className="eyebrow">{invite ? "CLIENT INVITATION" : "A LITTLE LESS BUSYWORK."}</p><h1>{mode === "signup" ? (invite ? "Meet your assistant." : "Let’s grow, together.") : "Welcome back."}</h1>
      <p className="muted">{invite ? "Create your client account to ask questions and request a service." : "Your leads, conversations, and voice assistant. One thoughtful workspace."}</p>
      <form onSubmit={authenticate}>
        {mode === "signup" && <label>Your name<input name="name" autoComplete="name" required maxLength={100} placeholder="What should we call you?" /></label>}
        <label>Email address<input name="email" type="email" autoComplete="email" required placeholder="you@business.com" /></label>
        <label>Password<input name="password" type="password" minLength={8} maxLength={72} autoComplete={mode === "signup" ? "new-password" : "current-password"} required placeholder="At least 8 characters" /></label>
        {error && <div className="alert error" role="alert"><WarningCircle />{error}</div>}
        <button className="primary" disabled={busy}>{busy ? <SpinnerGap className="spin" /> : <ArrowRight />}{mode === "signup" ? "Create your account" : "Open your workspace"}</button>
      </form>
      <button className="text-button" onClick={() => { setMode(mode === "signup" ? "login" : "signup"); setError(""); }}>{mode === "signup" ? "Already part of the story? Sign in" : "New here? Create an account"}</button>
      {localStorage.getItem("hydps2_token") && <button className="text-button" onClick={logout}><SignOut /> Clear saved session</button>}
      <p className="auth-note">AI-assisted. Human-approved. Always your call.</p>
    </div><div className="auth-bottom">Built for local ambition. Designed to feel simple.</div></section>
  </main>;
}
