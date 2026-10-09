"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { Microphone, Stop, PaperPlaneTilt, SpinnerGap, Waveform } from "@phosphor-icons/react";
import { api } from "./api";

type Message = { role: string; content: string; event_id?: string };
type Session = { conversation_token: string; conversation_id: string; dynamic_variables: Record<string, string>; user_id: string };

type VoiceProps = { client?: boolean; leadId?: string; onMessage?: (message: Message) => void };
export function VoiceControl(props: VoiceProps) {
  return <ConversationProvider><VoiceSession {...props} /></ConversationProvider>;
}
function VoiceSession({ client = false, leadId, onMessage }: VoiceProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useRef<string | null>(null);
  const pending = useRef<Message[]>([]);
  const seen = useRef(new Set<string>());
  const save = async (message: Message) => {
    if (!id.current) { pending.current.push(message); return; }
    try { await api(`/voice/sessions/${id.current}/messages`, { method: "POST", body: JSON.stringify({ ...message, event_id: message.event_id || crypto.randomUUID() }) }); }
    catch { setError("Voice is connected, but a transcript could not be saved. Please check memory service."); }
  };
  const conversation = useConversation({
    onMessage: (event) => {
      const eventId = `${event.role}:${event.response_id || event.event_id}`;
      if (seen.current.has(eventId)) return;
      seen.current.add(eventId);
      const message = { role: event.role === "user" ? "user" : "assistant", content: event.message, event_id: eventId };
      if (!message.content) return;
      onMessage?.(message); void save(message);
    },
    onError: (message) => { setError(typeof message === "string" ? message : "Voice connection failed. You can still use text chat."); setBusy(false); },
    clientTools: {
      query_pipeline: async (params) => JSON.stringify(await api(`/voice/tools/pipeline?location=${encodeURIComponent(String(params.location || ""))}`)),
      save_intake: async (params) => {
        if (!client) return JSON.stringify({ error: "This is a demo. Use the appointment request form in the client portal to save intake." });
        return JSON.stringify(await api("/client/intake", { method: "POST", body: JSON.stringify(params) }));
      },
    },
  });
  const connected = conversation.status === "connected";
  async function toggle() {
    if (connected) { await conversation.endSession(); id.current = null; return; }
    setBusy(true); setError(""); pending.current = []; seen.current.clear();
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      mic.getTracks().forEach(track => track.stop());
      const session = await api<Session>("/voice/session", { method: "POST", body: JSON.stringify({ lead_id: leadId }) });
      id.current = session.conversation_id;
      await conversation.startSession({ conversationToken: session.conversation_token, connectionType: "webrtc", dynamicVariables: session.dynamic_variables, userId: session.user_id });
      for (const message of pending.current.splice(0)) await save(message);
    } catch (err) { setError(err instanceof Error ? err.message : "Microphone access is required for voice."); id.current = null; }
    finally { setBusy(false); }
  }
  return <div className="voice-control"><button type="button" className={connected ? "danger" : "compact"} disabled={busy || conversation.status === "connecting"} onClick={() => void toggle()}>
    {busy ? <SpinnerGap className="spin" /> : connected ? <Stop weight="fill" /> : <Microphone />}
    {connected ? (conversation.isSpeaking ? "Speaking · End call" : "Listening · End call") : "Start voice call"}
  </button>{error && <p className="inline-error" role="alert">{error}</p>}</div>;
}

export function Assistant({ client = false }: { client?: boolean }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    const load = () => api<Message[]>(client ? "/client/history" : "/voice/operator-history").then(data => { if (active) setMessages(data); });
    void load().catch(err => { if (active) setError(err.message); });
    const timer = setInterval(() => { if (client && document.visibilityState === "visible") void load().catch(() => {}); }, 10000);
    return () => { active = false; clearInterval(timer); };
  }, [client]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [messages.length]);
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const message = String(new FormData(form).get("message") || "").trim();
    if (!message) return; setBusy(true); setError("");
    try {
      const result = await api<{ reply: string }>(client ? "/client/chat" : "/voice/operator-call-assistant", { method: "POST", body: JSON.stringify({ message }) });
      setMessages(current => [...current, { role: "user", content: message }, { role: "assistant", content: result.reply }]); form.reset();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not send message."); }
    finally { setBusy(false); }
  }
  return <section className="panel assistant-panel"><div className="panel-head"><div><p className="eyebrow">{client ? "YOUR BUSINESS ASSISTANT" : "USER-SIDE ASSISTANT"}</p><h2>Let’s talk it through.</h2></div><Waveform size={28} /></div>
    <div className="assistant-intro"><p>{client ? "Ask about services or share where and when you need help. Bookings need human confirmation." : "Ask about your pipeline, review a service idea, or work through a reply. Actions stay under your control."}</p><VoiceControl client={client} onMessage={message => setMessages(current => [...current, message])} /></div>
    <div className="chat-log" aria-live="polite">{messages.length === 0 && <div className="chat-empty">{client ? "What can we help you with today?" : "Try: How is my Hyderabad pipeline looking?"}<small>Text and voice conversations are saved to your account.</small></div>}
      {messages.map((message, index) => <div key={index} className={`chat-message ${message.role === "user" ? "mine" : ""}`}><small>{message.role === "user" ? "You" : "Assistant"}</small><p>{message.content}</p></div>)}<div ref={end} /></div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <form onSubmit={send} className="chat-compose"><label className="sr-only" htmlFor="chat-message">Message assistant</label><input id="chat-message" name="message" maxLength={4000} placeholder="Type your question…" required disabled={busy} autoComplete="off" /><button className="primary" disabled={busy} aria-label="Send message">{busy ? <SpinnerGap className="spin" /> : <PaperPlaneTilt />}</button></form>
  </section>;
}
