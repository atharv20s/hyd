"use client";
import { FormEvent, useEffect, useState } from "react";
import { Bell, CalendarBlank, Clock, DownloadSimple } from "@phosphor-icons/react";
import { api } from "./api";
import { RunAction } from "./campaign-desk";

export type Notification = { id: string; title: string; body: string; read: boolean; lead_id: string | null };
export type Meeting = { id: string; lead_id: string; message_id: string; business_name: string; title: string; starts_at: string | null; duration_minutes: number; location: string; source_quote: string; status: string };
export type Followup = { id: string; business_name: string; due_at: string; status: string };
type ThreadMessage = { id: string; direction: string; body: string; status: string; created_at: string };

function instant(value: string) { return new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z"); }
export function localInput(value: string | null) {
  if (!value) return "";
  const time = instant(value);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(time);
  const get = (kind: string) => parts.find(part => part.type === kind)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}
export function showTime(value: string) { return instant(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }) + " IST"; }

export function Notifications({ rows, busy, action }: { rows: Notification[]; busy: boolean; action: RunAction }) {
  if (!rows.length) return null;
  return <section className="panel"><div className="panel-head"><h2><Bell /> Your agent&apos;s updates</h2><span className="count">{rows.filter(row => !row.read).length} unread</span></div><div className="notification-list">{rows.slice(0, 8).map(row => <article className={row.read ? "read" : "unread"} key={row.id}><div><strong>{row.title}</strong><p>{row.body}</p></div>{!row.read && <button className="compact" disabled={busy} onClick={() => void action(`notice-${row.id}`, async () => { await api(`/notifications/${row.id}/read`, { method: "POST" }); })}>Mark read</button>}</article>)}</div></section>;
}

export function ScheduleDesk({ meetings, followups, busy, emailEnabled, action }: { meetings: Meeting[]; followups: Followup[]; busy: boolean; emailEnabled: boolean; action: RunAction }) {
  function confirm(event: FormEvent<HTMLFormElement>, meeting: Meeting) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    void action(`meeting-${meeting.id}`, async () => {
      const result = await api<{ message: string }>(`/meetings/${meeting.id}/confirm`, { method: "POST", body: JSON.stringify({ starts_at: String(form.get("starts")) + ":00+05:30", duration_minutes: Number(form.get("duration")), location: form.get("location") }) });
      return result.message;
    });
  }
  async function download(meeting: Meeting) {
    const result = await api<{ filename: string; content: string }>(`/meetings/${meeting.id}/calendar`);
    const url = URL.createObjectURL(new Blob([result.content], { type: "text/calendar;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); URL.revokeObjectURL(url);
  }
  return <section className="panel"><div className="panel-head"><div><p className="eyebrow">FROM A REPLY TO A REAL NEXT STEP</p><h2><CalendarBlank /> Meetings & follow-ups</h2></div><span className="count">{meetings.filter(row => row.status === "proposed").length} requests</span></div>
    <div className="schedule-list">{!meetings.length && <p className="muted">When an email asks for a meeting or demo, your agent adds its request here. Confirm the time and place, then send the calendar file.</p>}
      {meetings.map(meeting => <article key={meeting.id}><div className="message-heading"><h3>{meeting.business_name}</h3><span className="status">{meeting.status}</span></div><blockquote>{meeting.source_quote}</blockquote>
        {meeting.status === "proposed" ? <form className="strategy-form" onSubmit={event => confirm(event, meeting)}><div className="form-row"><label>Meeting time (IST)<input type="datetime-local" name="starts" required defaultValue={localInput(meeting.starts_at)} /></label><label>Duration (minutes)<input type="number" name="duration" min={10} max={240} defaultValue={meeting.duration_minutes} required /></label></div><label>Where will you meet?<input name="location" required maxLength={1000} defaultValue={meeting.location} placeholder="Office address or meeting link" /></label><div className="form-actions"><button className="primary" disabled={busy}>Confirm meeting</button><button type="button" className="compact" disabled={busy} onClick={() => void action(`decline-${meeting.id}`, async () => { await api(`/meetings/${meeting.id}/decline`, { method: "POST" }); return "Meeting request declined."; })}>Decline</button></div></form> : meeting.status === "confirmed" && <><p>{meeting.starts_at && showTime(meeting.starts_at)} · {meeting.duration_minutes} min<br />{meeting.location}</p><div className="form-actions"><button className="compact" disabled={busy} onClick={() => void action(`calendar-${meeting.id}`, () => download(meeting))}><DownloadSimple />Calendar file</button><button className="primary" disabled={busy || !emailEnabled} onClick={() => void action(`invite-${meeting.id}`, async () => { const result = await api<{ message: string }>(`/meetings/${meeting.id}/send-invitation`, { method: "POST" }); return result.message; })}>Send invitation by email</button></div></>}
      </article>)}
      {followups.length > 0 && <><h3>Scheduled follow-ups</h3>{followups.map(row => <article key={row.id}><div className="message-heading"><div><strong>{row.business_name}</strong><p><Clock /> {showTime(row.due_at)}</p></div><span className="status">{row.status.replaceAll("_", " ")}</span></div>{row.status === "scheduled" && <button className="compact" disabled={busy} onClick={() => void action(`cancel-${row.id}`, async () => { await api(`/followups/${row.id}/cancel`, { method: "POST" }); return "Follow-up cancelled."; })}>Cancel follow-up</button>}</article>)}</>}
    </div></section>;
}

export function FollowupForm({ leadId, busy, action }: { leadId: string; busy: boolean; action: RunAction }) {
  return <form className="inline-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(`followup-${leadId}`, async () => { await api("/followups", { method: "POST", body: JSON.stringify({ lead_id: leadId, due_at: String(form.get("due")) + ":00+05:30" }) }); return "Follow-up scheduled. Your agent will prepare a draft at that time and notify you for review."; }); }}><label>Prepare a follow-up (IST)<input type="datetime-local" name="due" required /></label><button className="compact" disabled={busy}><Clock />Schedule follow-up</button></form>;
}

export function ConversationHistory({ leadId }: { leadId: string }) {
  const [open, setOpen] = useState(false); const [rows, setRows] = useState<ThreadMessage[]>([]); const [error, setError] = useState("");
  useEffect(() => { let active = true; if (open) api<ThreadMessage[]>(`/communications/history/${leadId}`).then(data => { if (active) setRows(data); }).catch(err => { if (active) setError(err.message); }); return () => { active = false; }; }, [open, leadId]);
  return <div className="conversation-history"><button className="compact" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide conversation" : "Read conversation history"}</button>{open && <div>{error && <p role="alert">{error}</p>}{rows.map(row => <article key={row.id}><small>{row.direction === "inbound" ? "Business" : "Your team"} · {showTime(row.created_at)} · {row.status.replaceAll("_", " ")}</small><p className="message-body">{row.body}</p></article>)}</div>}</div>;
}
