"use client";
import { FormEvent, useRef } from "react";
import { ArrowRight, Pause, Play, Sparkle } from "@phosphor-icons/react";
import { api, User } from "./api";

export type Campaign = { id: string; service_description: string; target_clients: string; plan: { keywords?: string[]; city?: string; rationale?: string }; status: string; paused: boolean; counts: Record<string, number>; total: number };
export type RunAction = (name: string, run: () => Promise<string | void>) => Promise<void>;

export function CampaignDesk({ user, campaigns, busy, action, onStarted, onResults }: {
  user: User; campaigns: Campaign[]; busy: boolean; action: RunAction; onStarted: (user: User) => void; onResults: (id: string) => void;
}) {
  const request = useRef("");
  const lastInputs = useRef("");
  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const service = String(form.get("service") || "");
    const clients = String(form.get("clients") || "");
    const signature = JSON.stringify([service, clients]);
    if (!request.current || signature !== lastInputs.current) { request.current = crypto.randomUUID(); lastInputs.current = signature; }
    void action("campaign", async () => {
      await api("/campaigns", { method: "POST", body: JSON.stringify({ service_description: service, target_clients: clients, request_id: request.current }) });
      request.current = "";
      onStarted(await api<User>("/auth/me"));
      return "Your campaign has started. The agent will choose searches, discover businesses, and prepare the first ten ideas for review.";
    });
  }
  return <>
    <section className="panel campaign-start"><div className="panel-head"><div><p className="eyebrow">TWO ANSWERS. YOUR NEXT CONNECTION.</p><h2>Tell your agent what matters.</h2></div><Sparkle size={28} /></div>
      <form className="panel-body strategy-form" onSubmit={start}>
        <label>What&apos;s your service?<textarea name="service" required rows={3} maxLength={4000} defaultValue={user.service_description} placeholder="I help salons manage enquiries and appointment requests with an AI receptionist." /></label>
        <label>Who are your clients?<textarea name="clients" required rows={3} maxLength={4000} defaultValue={user.target_clients} placeholder="Independent salons in Banjara Hills and Jubilee Hills, Hyderabad." /></label>
        <div className="form-footer"><small className="muted">Your agent plans the search and prepares useful ideas. Messages wait for your review.</small><button className="primary" disabled={busy}>Start my campaign <ArrowRight /></button></div>
      </form>
    </section>
    <section className="panel"><div className="panel-head"><h2>Your campaigns</h2><span className="count">{campaigns.length} recent searches</span></div>
      {campaigns.length ? <div className="campaign-list">{campaigns.map(campaign => <article key={campaign.id}>
        <div className="message-heading"><div><p className="eyebrow">{campaign.plan.city || "PLANNING YOUR SEARCH"}</p><h3>{campaign.target_clients}</h3></div><span className="status">{campaign.paused ? "paused" : campaign.status.replaceAll("_", " ")}</span></div>
        <p>{campaign.service_description}</p>
        {campaign.plan.keywords && <div className="search-chips">{campaign.plan.keywords.map(word => <span key={word}>{word}</span>)}</div>}
        <p className="muted">{campaign.plan.rationale || "Your orchestrator is selecting relevant business categories."}</p>
        <div className="campaign-progress"><span><strong>{campaign.total}</strong> businesses</span><span><strong>{(campaign.counts.drafted || 0) + (campaign.counts.approved || 0)}</strong> for review</span><span><strong>{campaign.counts.replied || 0}</strong> replied</span></div>
        <div className="form-actions"><button className="primary" disabled={busy} onClick={() => onResults(campaign.id)}>View this search <ArrowRight /></button>
          <button className="compact" disabled={busy} onClick={() => void action(`campaign-${campaign.id}`, async () => { await api(`/campaigns/${campaign.id}/${campaign.paused ? "resume" : "pause"}`, { method: "POST" }); return campaign.paused ? "Campaign resumed." : "Campaign paused. The current step will finish; new steps and sends will wait."; })}>{campaign.paused ? <Play /> : <Pause />}{campaign.paused ? "Resume" : "Pause"}</button></div>
      </article>)}</div> : <div className="empty"><Sparkle /><h3>Your agent is ready.</h3><p>Two answers above start your first search. Add an area in the second answer, or we&apos;ll start in Hyderabad.</p></div>}
    </section>
  </>;
}
