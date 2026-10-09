"use client";
import { ChangeEvent, Dispatch, FormEvent, ReactNode, SetStateAction, useCallback, useEffect, useRef, useState } from "react";
import { ArrowClockwise, ArrowRight, ArrowUpRight, Brain, Buildings, Check, ChatsCircle, Clock, Link, MagnifyingGlass, MapPin, Plus, SignOut, Sparkle, SpinnerGap, Tray, UploadSimple, WarningCircle, X } from "@phosphor-icons/react";
import { api, User } from "./api";
import { Brand, Charminar, HyderabadLine } from "./brand";
import { Assistant, VoiceControl } from "./assistant";
import { DiscoveryForm, DiscoveryRequest } from "./discovery-form";
import { ReplySimulator } from "./reply-simulator";
import { Notifications, ScheduleDesk, Notification, Meeting, Followup } from "./communication-tools";

type Lead = { id: string; title: string; category: string; address: string; phone: string; emails: string; website: string; review_rating: number | null; review_count: number | null; proposed_idea: string; pitch_hook: string; draft_subject: string; draft_body: string; outreach_status: string };
type Job = { id: string; keywords: string[]; city: string; status: string; lead_count: number; error_message?: string; location_source: string; radius_m: number; drafts_queued: number; excluded_count: number };
type Task = { id: string; kind: string; status: string; error_message?: string; available_at: string };
type InboxMessage = { id: string; lead_id: string; business_name: string; body: string; subject: string; classification: string; status: string; draft_response: string; direction: string; channel: string };
type Intake = { _id: string; business_name: string; service: string; location: string; preferred_at: string; contact: string; status: string };
type Decision = { decision_id: string; human_action: string; feedback_tag?: string; notes?: string; final_content: { subject?: string; body?: string }; checkpoint_type: string };
type System = { dependencies: Record<string, string>; worker: string; llm_configured: boolean; voice_configured: boolean; email_enabled: boolean; dry_run: boolean; email_provider: string; sender: string; reply_webhook_configured: boolean; sms_enabled: boolean; sms_webhook_configured: boolean };
type Pipeline = { total: number; by_status: Record<string, number> };
type Tab = "overview" | "discover" | "leads" | "inbox" | "assistant" | "memory";
const nav: { key: Tab; label: string; icon: ReactNode }[] = [
  { key: "overview", label: "Your workspace", icon: <Buildings /> }, { key: "discover", label: "Discover businesses", icon: <MapPin /> },
  { key: "leads", label: "Lead pipeline", icon: <Sparkle /> }, { key: "inbox", label: "Communication", icon: <Tray /> },
  { key: "assistant", label: "Talk to your agent", icon: <ChatsCircle /> }, { key: "memory", label: "Memory & strategy", icon: <Brain /> },
];
function Status({ value }: { value: string }) { return <span className={`status status-${value}`}>{value.replaceAll("_", " ")}</span>; }
function Empty({ title, children }: { title: string; children: ReactNode }) { return <div className="empty"><Sparkle /><h3>{title}</h3><p>{children}</p></div>; }
function date(value: string) { return new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }); }

export function Dashboard({ user, setUser, logout }: { user: User; setUser: Dispatch<SetStateAction<User | null>>; logout: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [inbox, setInbox] = useState<InboxMessage[]>([]);
  const [intakes, setIntakes] = useState<Intake[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [followups, setFollowups] = useState<Followup[]>([]);
  const [rules, setRules] = useState<string[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [system, setSystem] = useState<System | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline>({ total: 0, by_status: {} });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [live, setLive] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [activeId, setActiveId] = useState("");
  const [invite, setInvite] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      api<Lead[]>(`/leads?limit=200&offset=${offset}`), api<Job[]>("/jobs"), api<Task[]>("/tasks"),
      api<InboxMessage[]>("/communications"), api<Intake[]>("/client-requests"), api<Pipeline>("/voice/tools/pipeline"),
      api<Notification[]>("/notifications"), api<Meeting[]>("/meetings"), api<Followup[]>("/followups"),
    ]);
    if (results[0].status === "fulfilled") setLeads(results[0].value);
    if (results[1].status === "fulfilled") setJobs(results[1].value);
    if (results[2].status === "fulfilled") setTasks(results[2].value);
    if (results[3].status === "fulfilled") setInbox(results[3].value);
    if (results[4].status === "fulfilled") setIntakes(results[4].value);
    if (results[5].status === "fulfilled") setPipeline(results[5].value);
    if (results[6].status === "fulfilled") setNotifications(results[6].value);
    if (results[7].status === "fulfilled") setMeetings(results[7].value);
    if (results[8].status === "fulfilled") setFollowups(results[8].value);
    const failed = results.find(result => result.status === "rejected");
    if (failed?.status === "rejected") setError(failed.reason.message);
  }, [offset]);
  const loadMemory = useCallback(async () => {
    const [preferences, examples] = await Promise.all([api<{ rules: string[] }>("/decisions/rules"), api<Decision[]>("/decisions?limit=20")]);
    setRules(preferences.rules); setDecisions(examples);
  }, []);
  useEffect(() => {
    void load(); let active = true;
    api<System>("/system/status").then(data => { if (active) setSystem(data); }).catch(err => setError(err.message));
    void loadMemory().catch(err => setError(err.message));
    const timer = setInterval(() => { if (document.visibilityState === "visible") void load(); }, 10000);
    const statusTimer = setInterval(() => { if (document.visibilityState === "visible") api<System>("/system/status").then(data => { if (active) setSystem(data); }).catch(() => { if (active) setSystem(null); }); }, 60000);
    return () => { active = false; clearInterval(timer); clearInterval(statusTimer); };
  }, [load, loadMemory]);
  useEffect(() => {
    let stopped = false; let socket: WebSocket | null = null; let timer: ReturnType<typeof setTimeout>;
    async function connect() {
      try {
        const { ticket } = await api<{ ticket: string }>("/communications/ws-ticket");
        if (stopped) return;
        const base = process.env.NEXT_PUBLIC_WS_URL || (location.hostname === "localhost" || location.hostname === "127.0.0.1" ? `ws://${location.hostname}:8000` : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`);
        socket = new WebSocket(`${base}/api/v1/communications/ws`);
        socket.onopen = () => socket?.send(JSON.stringify({ ticket }));
        socket.onmessage = event => { const data = JSON.parse(event.data); setLive(data.type !== "fallback"); if (data.type !== "connected" && data.type !== "fallback") void load(); };
        socket.onerror = () => setLive(false);
        socket.onclose = () => { setLive(false); if (!stopped) timer = setTimeout(connect, 15000); };
      } catch { setLive(false); if (!stopped) timer = setTimeout(connect, 15000); }
    }
    void connect(); return () => { stopped = true; clearTimeout(timer); socket?.close(); };
  }, [load]);
  async function action(name: string, run: () => Promise<string | void>) {
    setBusy(name); setError(""); setNotice("");
    try { const result = await run(); if (result) setNotice(result); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not complete this action."); }
    finally { setBusy(""); }
  }
  const counts = pipeline.by_status;
  const canEmail = Boolean(system?.dry_run || system?.email_enabled);
  const active = leads.find(lead => lead.id === activeId);
  const visible = leads.filter(lead => (!filter || lead.outreach_status === filter) && `${lead.title} ${lead.address} ${lead.category}`.toLowerCase().includes(search.toLowerCase()));
  const healthy = system && Object.values(system.dependencies).every(value => value === "online") && system.worker === "online";
  const reviewCount = (counts.drafted || 0) + (counts.approved || 0);
  const pendingIntakes = intakes.filter(request => request.status === "pending_confirmation");
  const queueDraft = (ids: string[]) => action("draft", async () => {
    const result = await api<{ queued: number }>("/leads/batch-draft", { method: "POST", body: JSON.stringify({ lead_ids: ids }) });
    setSelected([]); return `${result.queued} draft${result.queued === 1 ? "" : "s"} queued. Nothing will send without your approval.`;
  });
  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    await action("import", async () => {
      if (file.size > 2_000_000) throw new Error("Please choose a CSV smaller than 2 MB.");
      const result = await api<{ imported: number }>("/leads/import", { method: "POST", body: JSON.stringify({ csv_text: await file.text() }) });
      return `${result.imported} new businesses imported. Existing records were kept.`;
    }); event.target.value = "";
  }
  async function discover(request: DiscoveryRequest) {
    await action("discover", async () => {
      await api("/jobs", { method: "POST", body: JSON.stringify(request) });
      return `Live Maps discovery queued${request.location_source === "browser" ? ` within ${request.radius_m / 1000} km of your shared location` : ""}. ${request.prepare_drafts ? "Eligible new businesses will get drafts for your review." : "The worker will collect business details and available emails."} No outreach has been sent.`;
    });
  }
  async function strategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await action("strategy", async () => { const next = await api<User>("/onboarding", { method: "POST", body: JSON.stringify({ service_description: form.get("service"), target_clients: form.get("clients") }) }); setUser(next); return "Strategy saved. New ideas and drafts will use your offer."; });
  }
  async function decide(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!active) return; const form = new FormData(event.currentTarget); const button = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement;
    await action("decision", async () => {
      const result = await api<{ message: string }>(`/leads/${active.id}/decision`, { method: "POST", body: JSON.stringify({ action: button.value === "reject" ? "rejected" : (String(form.get("body")) !== active.draft_body || String(form.get("subject")) !== active.draft_subject ? "edited" : "approved"), edited_subject: form.get("subject"), edited_body: form.get("body"), feedback_tag: form.get("feedback") || undefined, send_email: button.value === "send" }) });
      await loadMemory(); return result.message;
    });
  }
  async function inboxAction(message: InboxMessage, kind: string, body?: string, feedback?: string) {
    await action(`inbox-${message.id}`, async () => {
      const result = await api<{ message: string }>(`/communications/${message.id}/action`, { method: "POST", body: JSON.stringify({ action: kind, body: body || "", feedback: feedback || "" }) });
      await loadMemory(); return result.message;
    });
  }
  function replyDecision(event: FormEvent<HTMLFormElement>, message: InboxMessage) {
    event.preventDefault(); const form = new FormData(event.currentTarget); const button = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement;
    void inboxAction(message, button.value, String(form.get("body")), String(form.get("feedback")));
  }
  return <main className="app-shell"><aside className="sidebar"><Brand /><p className="sidebar-label">YOUR GROWTH DESK</p><nav aria-label="Primary navigation">{nav.map(item => <button key={item.key} className={tab === item.key ? "active" : ""} onClick={() => { setTab(item.key); setActiveId(""); setInvite(""); }}>{item.icon}{item.label}{item.key === "inbox" && pendingIntakes.length > 0 && <span className="nav-count">{pendingIntakes.length}</span>}</button>)}</nav>
    <div className="sidebar-city"><Charminar /><HyderabadLine /><small>One city. Endless possibilities.</small></div><button className="profile" onClick={logout}><span className="avatar">{(user.full_name || user.email)[0].toUpperCase()}</span><span>{user.full_name || user.email}<small>Operator · Sign out</small></span><SignOut /></button></aside>
    <section className="workspace"><header className="workspace-header"><div><p className="eyebrow">HYDERABAD / YOUR COMMAND CENTER</p><h1>{tab === "overview" ? `Hello, ${user.full_name.split(" ")[0] || "there"}.` : nav.find(item => item.key === tab)?.label}</h1><p className="header-note">A little local insight. A lot of possibility.</p></div><div className="header-tools"><span className={`live ${healthy ? "" : "degraded"}`}><i />{healthy ? "Services online" : system ? "Services need attention" : "Checking services"}</span><button className="icon-button" aria-label="Refresh workspace" disabled={!!busy} onClick={() => void action("refresh", async () => { setSystem(await api<System>("/system/status")); })}><ArrowClockwise /></button></div></header>
    {(error || notice) && <div className={`alert ${error ? "error" : "success"}`} role={error ? "alert" : "status"}>{error ? <WarningCircle /> : <Check />}<span>{error || notice}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => { setError(""); setNotice(""); }}><X /></button></div>}
    {tab === "overview" && <>
      <section className="welcome-card"><div><p className="eyebrow">BUILT AROUND YOUR NEXT YES.</p><h2>Your city is full of<br />good connections.</h2><p>Discover the right businesses, choose a useful service idea, and start a conversation that sounds like you.</p><button className="primary" onClick={() => setTab("discover")}>Find your next opportunity <ArrowUpRight /></button></div><div className="welcome-art"><Charminar /><span>HYDERABAD, IN GOOD COMPANY.</span></div></section>
      <section className="stats" aria-label="Whole pipeline summary">{[["Businesses found", pipeline.total], ["Ready for your review", reviewCount], ["Conversations started", (counts.contacted || 0) + (counts.replied || 0)], ["Do not contact", counts.opted_out || 0]].map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
      <div className="overview-grid"><section className="panel"><div className="panel-head"><div><p className="eyebrow">THE NEXT MOVE</p><h2>Keep things moving.</h2></div><Sparkle /></div><div className="next-actions"><button onClick={() => setTab("memory")}><span className="step-number">01</span><span><strong>{user.service_description ? "Refine your offer" : "Tell us what you do"}</strong><small>Your service shapes every pitch.</small></span><ArrowRight /></button><button onClick={() => setTab("leads")}><span className="step-number">02</span><span><strong>Review {reviewCount} drafts</strong><small>You approve. Then we send.</small></span><ArrowRight /></button><button onClick={() => setTab("inbox")}><span className="step-number">03</span><span><strong>Open your communication hub</strong><small>Replies, callbacks, and appointment requests.</small></span><ArrowRight /></button></div></section>
      <section className="panel system-panel"><div className="panel-head"><div><p className="eyebrow">ACTUAL SERVICE STATUS</p><h2>Behind the scenes.</h2></div><span className={`tiny-dot ${live ? "online" : ""}`} /></div><div className="system-list">{Object.entries(system?.dependencies || { database: "checking", memory: "checking", cache: "checking" }).map(([label, value]) => <div key={label}><span>{label === "memory" ? "MongoDB memory" : label === "cache" ? "Redis cache" : "SQL accounts & pipeline"}</span><Status value={value} /></div>)}<div><span>Concurrent worker</span><Status value={system?.worker || "checking"} /></div><div><span>Live inbox updates</span><Status value={live ? "online" : "polling"} /></div><div><span>Email delivery</span><Status value={system?.dry_run ? "dry run — MailHog" : system?.email_enabled ? "enabled" : "disabled"} /></div><div><span>SMS via your Android SIM</span><Status value={system?.sms_enabled ? "enabled" : system?.sms_webhook_configured ? "awaiting_phone" : "not_configured"} /></div><div><span>Voice agents</span><Status value={system?.voice_configured ? "configured" : "not_configured"} /></div></div></section></div>
    </>}
    {tab === "discover" && <><DiscoveryForm busy={!!busy} hasStrategy={!!user.service_description} onDiscover={discover} /><section className="import-card"><UploadSimple size={28} /><div><h3>Already have a list?</h3><p>Import a scraper CSV with a title column. Duplicates are skipped.</p></div><button className="compact" disabled={!!busy} onClick={() => fileRef.current?.click()}>Choose CSV</button></section>
      <section className="panel"><div className="panel-head"><h2>Discovery jobs</h2><span className="count">{jobs.length} recent jobs</span></div>{jobs.length ? <div className="job-list">{jobs.map(job => <article key={job.id}><div><strong>{job.keywords.join(", ")}</strong><small><MapPin />{job.location_source === "browser" ? `Device location · ${job.radius_m / 1000} km radius` : job.city} · {job.lead_count} businesses imported</small>{job.location_source === "browser" && <small>{job.excluded_count} outside radius / missing coordinates</small>}{job.drafts_queued > 0 && <small>{job.drafts_queued} outreach drafts queued for review</small>}{job.error_message && <p className="inline-error">{job.error_message}</p>}</div><Status value={job.status} /></article>)}</div> : <Empty title="Your next search starts here.">Queue a Hyderabad neighbourhood or import an existing list.</Empty>}</section></>}
    {tab === "leads" && <>
      {!user.service_description && <div className="alert warning">Save your service and target clients in Memory & strategy before drafting.<button className="compact" onClick={() => setTab("memory")}>Set up strategy</button></div>}
      <section className="panel"><div className="panel-head"><div><p className="eyebrow">SCOUT → CRITIC → YOUR APPROVAL</p><h2>Good prospects. Useful ideas.</h2></div><button className="compact" disabled={!!busy} onClick={() => fileRef.current?.click()}><UploadSimple />Import</button></div><div className="table-toolbar"><label className="search-field"><MagnifyingGlass /><input aria-label="Search businesses on this page" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search business, area, category…" /></label><select aria-label="Filter by status" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All statuses</option>{["new", "queued", "processing", "drafted", "approved", "contacted", "replied", "opted_out", "rejected", "draft_failed"].map(value => <option key={value}>{value}</option>)}</select><button className="primary" disabled={!selected.length || !!busy || !user.service_description} onClick={() => void queueDraft(selected)}><Sparkle />Draft selected ({selected.length})</button></div>
      {visible.length ? <div className="table-wrap"><table><thead><tr><th><span className="sr-only">Select business</span></th><th>Business</th><th>Contact</th><th>Status</th><th>Next step</th></tr></thead><tbody>{visible.map(lead => {
        const blocked = ["queued", "processing", "contacted", "replied", "opted_out"].includes(lead.outreach_status);
        return <tr key={lead.id}><td><input type="checkbox" aria-label={`Select ${lead.title}`} checked={selected.includes(lead.id)} disabled={blocked} onChange={event => setSelected(current => event.target.checked ? [...current, lead.id].slice(0, 100) : current.filter(id => id !== lead.id))} /></td><td><button className="business-button" onClick={() => { setActiveId(lead.id); setInvite(""); }}><strong>{lead.title}</strong></button><small>{lead.category || "Local business"}</small><small>{lead.address || "Address not listed"}</small></td><td>{lead.emails || lead.phone || "Not listed"}<small>{lead.review_rating ? `${lead.review_rating}/5 · ${lead.review_count || 0} reviews` : "No review data"}</small></td><td><Status value={lead.outreach_status} /></td><td>{lead.draft_body || blocked ? <button className="compact" onClick={() => { setActiveId(lead.id); setInvite(""); }}>View <ArrowUpRight /></button> : <button className="compact" disabled={!!busy || !user.service_description} onClick={() => void queueDraft([lead.id])}><Sparkle />Draft pitch</button>}</td></tr>;
      })}</tbody></table></div> : <Empty title="Room for your next connection.">{leads.length ? "No businesses match this search or filter." : "Discover businesses or import a CSV to start your pipeline."}</Empty>}
      <div className="pagination"><span>{offset + (leads.length ? 1 : 0)}–{offset + leads.length} of {pipeline.total} · filters apply to this page</span><div><button className="compact" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 200)); setSelected([]); }}>Previous</button><button className="compact" disabled={offset + 200 >= pipeline.total} onClick={() => { setOffset(offset + 200); setSelected([]); }}>Next</button></div></div></section>
      {active && <section className="panel review-panel"><div className="panel-head"><div><p className="eyebrow">YOUR HUMAN CHECKPOINT</p><h2>{active.title}</h2></div><button className="icon-button" aria-label="Close business review" onClick={() => setActiveId("")}><X /></button></div><div className="panel-body"><div className="review-summary"><div><p className="eyebrow">SELECTED SERVICE IDEA</p><h3>{active.pitch_hook || "No idea prepared yet"}</h3><p>{active.proposed_idea || "Queue a draft to run the Scout and Critic against your offer."}</p></div><div className="demo-actions"><VoiceControl leadId={active.id} /><button className="compact" disabled={!!busy} onClick={() => void action("invite", async () => { const result = await api<{ url: string }>(`/leads/${active.id}/invite`, { method: "POST" }); setInvite(result.url); return "Client invitation created. Share it with this business to open its portal."; })}><Link />Create client invitation</button></div></div>{invite && <label>Client invitation link<input readOnly value={invite} onFocus={event => event.target.select()} /><small>Expires after 7 days. Anyone with the link can create an account for this business.</small></label>}
      {active.draft_body && <form key={`${active.id}:${active.draft_subject}`} onSubmit={decide} className="review-form"><label>Subject<input name="subject" defaultValue={active.draft_subject} required maxLength={500} /></label><label>Outreach message<textarea name="body" defaultValue={active.draft_body} required rows={8} maxLength={10000} /></label><label>Teach the agent (optional)<input name="feedback" maxLength={1000} placeholder="e.g. Shorter opener; avoid salesy phrases" /></label><p className="compliance-note"><Check />AI-use disclosure and a signed one-click opt-out are appended at send time. STOP / not interested permanently stops contact.</p><div className="form-actions"><button value="save" className="primary" disabled={!!busy || !["drafted", "approved"].includes(active.outreach_status)}><Check />Approve & save</button><button value="send" className="compact" disabled={!!busy || !canEmail || !active.emails || !["drafted", "approved"].includes(active.outreach_status)}>{system?.dry_run ? "Approve & send to MailHog" : "Approve & send"} <ArrowUpRight /></button><button value="reject" formNoValidate className="danger" disabled={!!busy || !["drafted", "approved"].includes(active.outreach_status)}>Reject draft</button></div>{!canEmail && <small className="muted">Sending stays disabled until your Resend domain and sender are configured.</small>}</form>}</div></section>}
    </>}
    {tab === "inbox" && <>{system?.dry_run && <ReplySimulator leads={leads} onQueued={load} />}<Notifications rows={notifications} busy={!!busy} action={action} /><ScheduleDesk meetings={meetings} followups={followups} busy={!!busy} emailEnabled={canEmail} action={action} /><div className="inbox-intro"><h2>Every conversation, in your corner.</h2><p>Signed webhooks bring in email and SMS replies. Live updates keep this desk current. Nothing follows up on a refusal.</p><span className="count">{live ? "WebSocket connected" : "Polling every 10 seconds"} · {system?.dry_run ? "Dry-run replies · MailHog delivery only" : system?.sms_enabled ? "SMS gateway connected" : system?.reply_webhook_configured ? "Email reply routing configured" : "Receiving domain, or Textbee gateway, needed"}</span></div>
      <section className="panel"><div className="panel-head"><h2>Client requests</h2><span className="count">{pendingIntakes.length} need confirmation</span></div>{intakes.length ? <div className="request-list">{intakes.map(request => <article key={request._id}><div><strong>{request.business_name} · {request.service}</strong><p>{request.location} · {date(request.preferred_at)} IST</p><small>{request.contact}</small></div><Status value={request.status} />{request.status === "pending_confirmation" && <div className="form-actions">{["confirmed", "declined"].map(value => <button key={value} className="compact" disabled={!!busy} onClick={() => void action(`intake-${request._id}`, async () => { await api(`/client-requests/${request._id}/decision`, { method: "POST", body: JSON.stringify({ status: value }) }); return `Request ${value}.`; })}>{value === "confirmed" ? "Confirm" : "Decline"}</button>)}</div>}</article>)}</div> : <Empty title="No client requests yet.">Share a client invitation from a business preview. Requests will arrive here.</Empty>}</section>
      <section className="panel"><div className="panel-head"><h2>Client, email & SMS conversations</h2><span className="count">{inbox.length} recent messages</span></div>{inbox.length ? <div className="inbox-list">{inbox.map(message => <article key={message.id}><div className="message-heading"><div><strong>{message.business_name} · {message.channel}</strong><h3>{message.subject || (message.channel === "sms" ? "SMS reply" : "Email reply")}</h3></div><Status value={message.status} /></div><p className="message-body">{message.body}</p><div className="form-actions"><Status value={message.classification} /><button className="compact" disabled={!!busy || message.status === "suppressed" || message.status === "sent"} onClick={() => void action(`reply-${message.id}`, async () => { await api(`/communications/${message.id}/draft`, { method: "POST" }); return "Reply draft prepared. Please review it before sending."; })}><Sparkle />Prepare reply</button><button className="compact" disabled={!!busy || message.status === "suppressed"} onClick={() => void inboxAction(message, "handled")}>Mark handled</button><button className="danger" disabled={!!busy || message.status === "suppressed"} onClick={() => void inboxAction(message, "not_interested")}>Not interested · Stop contact</button></div>
      {message.draft_response && message.status !== "suppressed" && <form className="reply-form" key={`${message.id}:${message.draft_response}`} onSubmit={event => replyDecision(event, message)}><label>Review your reply<textarea rows={5} name="body" required defaultValue={message.draft_response} maxLength={10000} /></label><label>Learning feedback<input name="feedback" maxLength={1000} placeholder="What should future replies do differently?" /></label><div className="form-actions"><button value="save_reply" className="primary" disabled={!!busy || message.status === "sent"}>Save approved reply</button><button value="send_reply" className="compact" disabled={!!busy || (message.channel === "email" && !canEmail) || (message.channel === "sms" && !system?.sms_enabled) || message.status === "sent"}>{system?.dry_run ? "Approve & send reply to MailHog" : "Approve & send reply"}</button></div></form>}</article>)}</div> : <Empty title="The conversation starts with a hello.">Client chat appears automatically. Email needs Resend; SMS needs your paired Textbee Android phone and signed webhook.</Empty>}</section></>}
    {tab === "assistant" && <Assistant />}
    {tab === "memory" && <><section className="panel"><div className="panel-head"><div><p className="eyebrow">TWO ANSWERS, A BETTER AGENT</p><h2>Your offer. Your ideal client.</h2></div><Brain size={28} /></div><form className="panel-body strategy-form" onSubmit={strategy}><label>What do you offer?<textarea name="service" defaultValue={user.service_description} required maxLength={4000} rows={3} placeholder="AI receptionist and appointment intake for independent salons" /></label><label>Who do you want to work with?<textarea name="clients" defaultValue={user.target_clients || "Independent salons and clinics in Hyderabad"} required rows={3} maxLength={4000} /></label><button className="primary" disabled={!!busy}>Save strategy <Check /></button></form></section>
      <section className="panel"><div className="panel-head"><h2>How you like to communicate</h2><span className="count">{rules.length} saved preferences</span></div><div className="panel-body">{rules.length ? <div className="rule-list">{rules.map(rule => <div key={rule}><Check />{rule}</div>)}</div> : <p className="muted">Your corrections become reusable style preferences. Add your first one below.</p>}<form className="inline-form" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const rule = new FormData(form).get("rule"); void action("rule", async () => { await api("/decisions/rules", { method: "POST", body: JSON.stringify({ rule }) }); await loadMemory(); form.reset(); return "Preference saved. Future drafts will use it."; }); }}><input name="rule" required maxLength={1000} aria-label="New communication preference" placeholder="e.g. Keep it warm, specific, and under 80 words" /><button className="compact" disabled={!!busy}><Plus />Add preference</button></form></div></section>
      <section className="panel"><div className="panel-head"><h2>Learning from your decisions</h2><span className="count">Your latest 20 checkpoints</span></div>{decisions.length ? <div className="decision-list">{decisions.map(decision => <article key={decision.decision_id}><Status value={decision.human_action} /><strong>{decision.final_content.subject || decision.checkpoint_type.replaceAll("_", " ")}</strong><p>{decision.final_content.body}</p>{(decision.feedback_tag || decision.notes) && <small>Feedback: {decision.feedback_tag || decision.notes}</small>}</article>)}</div> : <Empty title="Your judgment is the best teacher.">Approve, edit, or reject a draft. Those examples shape the next one; no model fine-tuning is claimed.</Empty>}</section></>}
    {(tab === "discover" || tab === "leads" || tab === "inbox") && <section className="panel"><div className="panel-head"><h2>Background tasks</h2><span className="count">Concurrent, durable queue</span></div>{tasks.length ? <div className="task-list">{tasks.slice(0, 12).map(task => <article key={task.id}><span><Clock /><strong>{task.kind.replaceAll("_", " ")}</strong><small>{date(task.available_at)} IST</small></span><Status value={task.status} />{task.status === "failed" && <button className="compact" disabled={!!busy} onClick={() => void action(`retry-${task.id}`, async () => { await api(`/tasks/${task.id}/retry`, { method: "POST" }); return "Task queued for retry."; })}>Retry</button>}{task.error_message && <p className="inline-error">{task.error_message}</p>}</article>)}</div> : <Empty title="Nothing queued yet.">Discovery and batch drafting run here while you keep working.</Empty>}</section>}
    <input type="file" ref={fileRef} accept=".csv,text/csv" hidden onChange={event => void importFile(event)} /><footer className="workspace-footer"><span>Made for you. Made for <span lang="te">హైదరాబాద్</span>.</span><span>AI-ASSISTED · HUMAN-APPROVED</span></footer>
    </section></main>;
}
