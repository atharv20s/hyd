"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Check, Crosshair, MagnifyingGlass, MapPin, SpinnerGap } from "@phosphor-icons/react";

export type DiscoveryRequest = {
  keywords: string[]; city: string; depth: number; scheduled_at?: string;
  latitude?: string; longitude?: string; location_source: "manual" | "browser";
  location_captured_at?: string; radius_m: number; prepare_drafts: boolean; draft_limit: number;
};
type Position = { latitude: number; longitude: number; accuracy: number; capturedAt: number };

export function DiscoveryForm({ busy, hasStrategy, onDiscover }: {
  busy: boolean; hasStrategy: boolean; onDiscover: (request: DiscoveryRequest) => Promise<void>;
}) {
  const [source, setSource] = useState<"manual" | "browser">("manual");
  const [position, setPosition] = useState<Position | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState("");
  const [prepare, setPrepare] = useState(false);
  const [radius, setRadius] = useState(5000);
  const requestId = useRef(0);
  useEffect(() => () => { requestId.current += 1; }, []);

  function manualLocation() {
    requestId.current += 1;
    setSource("manual"); setPosition(null); setLocationError(""); setLocating(false);
  }
  function locate() {
    const id = ++requestId.current;
    setLocationError(""); setPosition(null); setSource("browser");
    if (!window.isSecureContext) {
      setLocationError("Location requires HTTPS or localhost. You can still enter an area manually."); return;
    }
    if (!navigator.geolocation) {
      setLocationError("This browser cannot share location. Enter an area manually instead."); return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(result => {
      if (id !== requestId.current) return;
      setLocating(false);
      setPosition({ latitude: result.coords.latitude, longitude: result.coords.longitude,
        accuracy: result.coords.accuracy, capturedAt: result.timestamp });
    }, error => {
      if (id !== requestId.current) return;
      setLocating(false);
      setLocationError(error.code === 1
        ? "Location permission was denied. Allow location in your browser settings, or enter an area manually."
        : error.code === 3 ? "Location timed out. Try again outdoors or enter an area manually."
        : "Your device could not determine its location. Try again or enter an area manually.");
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 });
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (source === "browser" && (!position || Date.now() - position.capturedAt > 300000)) {
      setLocationError("Please use my location again to get a fresh position before searching."); return;
    }
    await onDiscover({ keywords: String(form.get("keywords")).split(","),
      city: source === "browser" ? "Shared device location" : String(form.get("city")),
      depth: Number(form.get("depth")), radius_m: radius, location_source: source,
      ...(source === "browser" && position ? { latitude: String(position.latitude), longitude: String(position.longitude),
        location_captured_at: new Date(position.capturedAt).toISOString() } : {}),
      scheduled_at: form.get("schedule") ? new Date(String(form.get("schedule"))).toISOString() : undefined,
      prepare_drafts: prepare && hasStrategy, draft_limit: Number(form.get("draft_limit") || 10),
    });
  }
  return <section className="panel">
    <div className="panel-head"><div><p className="eyebrow">START WITH YOUR NEIGHBOURHOOD</p><h2>Who would you like to meet?</h2></div><MapPin size={26} aria-hidden="true" /></div>
    <form className="panel-body discovery-form" onSubmit={submit}>
      <label>Business categories<input name="keywords" required maxLength={2000} defaultValue="hair salon, dental clinic" placeholder="Comma-separated categories" /><small>Search Google Maps live, with up to 20 categories in one job.</small></label>
      <div className="location-picker">
        <div className="location-heading"><div><strong>Start close to home.</strong><p>Share your device location to find businesses nearby.</p></div>
          <button type="button" className="compact" disabled={busy || locating} onClick={locate} aria-describedby="location-privacy">
            {locating ? <SpinnerGap className="spin" aria-hidden="true" /> : <Crosshair aria-hidden="true" />}
            {locating ? "Finding you…" : position ? "Refresh my location" : "Use my location"}
          </button>
        </div>
        <p id="location-privacy" className="muted">Only when you choose. No background tracking. On submission, this search point is saved with your job and sent to the Maps scraper.</p>
        <div role="status" aria-live="polite">
          {locating && <p className="muted">Waiting for your device. Please allow location if your browser asks.</p>}
          {position && <div className="location-result"><Check aria-hidden="true" /><div><strong>Location captured · within about {Math.round(position.accuracy)} m</strong>
            <small>{position.latitude.toFixed(5)}, {position.longitude.toFixed(5)} · {new Date(position.capturedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</small>
            {position.accuracy > radius && <p className="inline-error">Device accuracy is wider than your search radius. Increase the radius or choose an area manually.</p>}
            <small>Scheduled searches use this saved point, not your location at the future start time.</small>
          </div></div>}
        </div>
        {locationError && <p className="inline-error" role="alert">{locationError}</p>}
        {source === "browser" && <button type="button" className="compact" onClick={manualLocation}>Use an area instead</button>}
      </div>
      {source === "manual" ? <label>Area / city<input name="city" list="hyderabad-areas" required maxLength={255} defaultValue="Banjara Hills, Hyderabad" />
        <datalist id="hyderabad-areas">{["Banjara Hills, Hyderabad", "Jubilee Hills, Hyderabad", "Gachibowli, Hyderabad", "Madhapur, Hyderabad", "Secunderabad, Hyderabad", "Hyderabad"].map(area => <option key={area} value={area} />)}</datalist>
        <small>Manual searches match your area text. Use device location for a strict radius.</small></label>
        : <label>Search within<select value={radius} onChange={event => setRadius(Number(event.target.value))}>
          {[500, 1000, 2000, 5000, 10000, 25000, 50000].map(value => <option key={value} value={value}>{value < 1000 ? `${value} m` : `${value / 1000} km`}</option>)}
        </select><small>Businesses outside this radius, or without usable coordinates, are excluded.</small></label>}
      <div className="form-row"><label>Search depth<input name="depth" type="number" min={1} max={10} defaultValue={5} required /></label><label>Start time (optional, your local time)<input name="schedule" type="datetime-local" /></label></div>
      <div className="discovery-next-step"><label className="checkbox-label"><input type="checkbox" checked={prepare && hasStrategy} disabled={!hasStrategy || busy} onChange={event => setPrepare(event.target.checked)} /><span>Prepare service ideas and outreach after discovery</span></label>
        <p className="muted">{hasStrategy ? "Uses your AI provider credits. New eligible leads go through Scout → Critic → draft. Nothing is sent or called automatically." : "Save your offer in Memory & strategy to enable automatic draft preparation."}</p>
        {prepare && hasStrategy && <label>Maximum drafts for this job<input name="draft_limit" type="number" min={1} max={100} defaultValue={10} required /></label>}
      </div>
      <div className="form-footer"><p className="muted">Live Maps data + available emails. You approve each outreach.</p><button className="primary" disabled={busy || locating || (source === "browser" && !position)}><MagnifyingGlass aria-hidden="true" />{busy ? "Queuing…" : "Discover businesses"}</button></div>
    </form>
  </section>;
}
