"use client";
import { FormEvent, useState } from "react";
import { api } from "./api";

export function ReplySimulator({ leads, onQueued }: {
  leads: { id: string; title: string; emails: string }[]; onQueued: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function simulate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setNotice("");
    try {
      const route = await api<{ from: string; to: string }>(`/communications/simulation-address/${form.get("lead")}`);
      await api("/communications/simulate-reply", { method: "POST", body: JSON.stringify({
        type: "email.received", data: { email_id: crypto.randomUUID(), from: route.from,
          to: [route.to], subject: "Re: Your proposal (demo)", text: form.get("reply") },
      }) });
      setNotice("Demo reply queued. The reply agent will prepare it for review; no live email is sent.");
      await onQueued();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not simulate the reply."); }
    finally { setBusy(false); }
  }
  const eligible = leads.filter(lead => lead.emails.trim());
  return <section className="panel">
    <div className="panel-head"><div><p className="eyebrow">LOCAL DEMO · NO LIVE DELIVERY</p><h2>Reply as this business.</h2></div></div>
    <form className="reply-form" onSubmit={simulate}>
      <label>Business<select name="lead" required disabled={busy || !eligible.length}>
        {eligible.map(lead => <option key={lead.id} value={lead.id}>{lead.title}</option>)}
      </select></label>
      <label>Incoming reply<textarea name="reply" required maxLength={10000} rows={3}
        defaultValue="Interested. Can we schedule a demo?" disabled={busy} /></label>
      <small>Try a question, meeting request, or STOP. STOP permanently blocks further outreach in this workspace.</small>
      <button className="primary" disabled={busy || !eligible.length}>{busy ? "Queueing reply…" : "Simulate incoming reply"}</button>
      {!eligible.length && <p>Import a business with an email address to try the simulator.</p>}
      <p role="status" aria-live="polite">{notice}</p>
    </form>
  </section>;
}
