import { useQuery } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { api, type Profile } from "../api";
import type { Units } from "../units";

// ------------------------------------------------------------------ profile / units
export function useProfile() {
  return useQuery({ queryKey: ["profile"], queryFn: () => api.get<Profile>("/profile"), staleTime: 60_000 });
}

export function useUnits(): Units {
  const { data } = useProfile();
  return data?.display_units ?? "imperial";
}

// ------------------------------------------------------------------ toast
type ToastState = { msg: string; error?: boolean } | null;
const ToastCtx = createContext<(msg: string, error?: boolean) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((msg: string, error = false) => {
    setToast({ msg, error });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), error ? 4500 : 2200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast${toast.error ? " error" : ""}`} role="status" aria-live="polite">
          {toast.msg}
        </div>
      )}
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/** Wrap a mutation so failures surface as a toast instead of vanishing. */
export function useRun() {
  const toast = useToast();
  return useCallback(
    async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
      try {
        const out = await fn();
        if (ok) toast(ok);
        return out;
      } catch (e) {
        toast(e instanceof Error ? e.message : "Something went wrong", true);
        return undefined;
      }
    },
    [toast],
  );
}

// ------------------------------------------------------------------ sheet
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="grabber" />
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="btn ghost icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ inputs
export function Stepper({
  value, onChange, step = 1, min = 0, max = 9999, digits = 0, label,
}: {
  value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number; digits?: number; label: string;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, Math.round(v / step) * step));
  const shown = Number.isFinite(value) ? value.toFixed(digits) : "";
  // While typing, keep the raw text: clamping or reformatting each keystroke turns "1" into the
  // minimum and makes values like 170 impossible to enter. Check and tidy it when typing ends.
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft == null) return;
    const v = parseFloat(draft.replace(",", "."));
    if (Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v)));
    setDraft(null);
  };
  return (
    <div className="stepper">
      <button type="button" onClick={() => onChange(clamp(value - step))} aria-label={`Less ${label}`}>−</button>
      <input
        inputMode="decimal" aria-label={label} value={draft ?? shown}
        onFocus={(e) => { setDraft(shown); e.target.select(); }}
        onChange={(e) => {
          const txt = e.target.value;
          setDraft(txt);
          // Pass along values already in range so a Save tapped mid-edit uses what's typed.
          const v = parseFloat(txt.replace(",", "."));
          if (Number.isFinite(v) && v >= min && v <= max) onChange(v);
        }}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      />
      <button type="button" onClick={() => onChange(clamp(value + step))} aria-label={`More ${label}`}>+</button>
    </div>
  );
}

export function Scale({
  value, onChange, n = 5, start = 1, label,
}: { value: number | null; onChange: (v: number | null) => void; n?: number; start?: number; label: string }) {
  return (
    <div className={`scale${n > 5 ? " ten" : ""}`} role="radiogroup" aria-label={label}>
      {Array.from({ length: n }, (_, i) => i + start).map((v) => (
        <button
          key={v} type="button" role="radio" aria-checked={value === v} className={value === v ? "on" : ""}
          onClick={() => onChange(value === v ? null : v)}
        >
          {v}
        </button>
      ))}
    </div>
  );
}

export function ScaleRow({ name, hint, ...rest }: { name: string; hint?: string } & Parameters<typeof Scale>[0]) {
  return (
    <div className="scale-row">
      <span className="name">
        {name}
        {hint && <span className="hint">{hint}</span>}
      </span>
      <Scale {...rest} />
    </div>
  );
}

export function Chips<T extends string>({
  options, value, onChange, labels,
}: { options: readonly T[]; value: T | null; onChange: (v: T) => void; labels?: Partial<Record<T, string>> }) {
  return (
    <div className="chips" role="radiogroup">
      {options.map((o) => (
        <button key={o} type="button" role="radio" aria-checked={value === o}
                className={`chip${value === o ? " on" : ""}`} onClick={() => onChange(o)}>
          {labels?.[o] ?? o.replace("_", " ")}
        </button>
      ))}
    </div>
  );
}

export function Stars({ value, onChange }: { value: number | null; onChange: (v: number) => void }) {
  return (
    <div className="stars" role="radiogroup" aria-label="Rate today's coaching">
      {[1, 2, 3, 4, 5].map((v) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} aria-label={`${v} of 5`}
                className={value != null && v <= value ? "on" : ""} onClick={() => onChange(v)}>
          ★
        </button>
      ))}
    </div>
  );
}

export function Bar({ value, target, max, hue }: { value: number; target: number | null; max?: number | null; hue?: string }) {
  const denom = target || max || 1;
  const pct = Math.min(100, (value / denom) * 100);
  const over = max != null && value > max;
  return (
    <div className={`bar${hue ? ` hue-${hue}` : ""}`} aria-hidden>
      <span className={over ? "over" : ""} style={{ width: `${pct}%` }} />
    </div>
  );
}

// ------------------------------------------------------------------ rings and gauges
/** A progress ring in one metric hue (hrv, heart, sleep, load, food, protein). Children sit in the centre. */
export function Ring({
  value, max = 100, size = 92, stroke = 8, hue, label, className = "", children,
}: {
  value: number | null | undefined; max?: number; size?: number; stroke?: number; hue: string; label: string;
  className?: string; children?: ReactNode;
}) {
  const gid = `ring${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const frac = value == null || !Number.isFinite(value) ? 0 : Math.max(0, Math.min(1, value / max));
  // Start empty and sweep to the value on mount.
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const t = requestAnimationFrame(() => setShown(frac));
    return () => cancelAnimationFrame(t);
  }, [frac]);
  return (
    <div className={`ring hue-${hue} ${className}`} role="img" aria-label={label} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: "var(--hue)", stopOpacity: "var(--ring-fade)" }} />
            <stop offset="1" style={{ stopColor: "var(--hue)" }} />
          </linearGradient>
        </defs>
        <circle className="track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} />
        <circle className="arc" cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${gid})`} strokeWidth={stroke}
                strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - shown)} strokeOpacity={frac > 0 ? 1 : 0} />
      </svg>
      <div className="center">{children}</div>
    </div>
  );
}

/** A range track with an optional highlighted band (e.g. a sweet spot) and a marker at the value. */
export function Gauge({ value, min, max, band, hue, ticks, label }: {
  value: number | null | undefined; min: number; max: number; band?: [number, number]; hue: string;
  ticks?: number[]; label: string;
}) {
  const pct = (v: number) => Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
  return (
    <div className={`hue-${hue}`} role="img" aria-label={label}>
      <div className="gauge">
        {band && <span className="band" style={{ left: `${pct(band[0])}%`, width: `${pct(band[1]) - pct(band[0])}%` }} />}
        {value != null && <span className="mark" style={{ left: `${pct(value)}%` }} />}
      </div>
      {ticks && (
        <div className="gauge-scale">
          {ticks.map((t) => <span key={t} style={{ left: `${pct(t)}%` }}>{t}</span>)}
        </div>
      )}
    </div>
  );
}

/** Sentence case for lowercase labels from the API ("quality run: intervals or tempo" → "Quality run: …"). */
export const cap = (s: string | null | undefined) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");

/** Icon and hue for a planned or logged session. */
export function sessionLook(type: string, sport?: string): { icon: string; hue: string } {
  if (type === "game" || sport === "ultimate") return { icon: "disc", hue: "t-game" };
  if (type === "strength" || sport === "strength") return { icon: "kettlebell", hue: "t-strength" };
  if (type === "rest") return { icon: "moon", hue: "t-rest" };
  if (type === "hard") return { icon: "bolt", hue: "t-hard" };
  if (type === "moderate") return { icon: "run", hue: "t-moderate" };
  return { icon: "run", hue: "t-easy" };
}

// ------------------------------------------------------------------ rendered coaching text
/** Renders the small markdown subset the coach uses: **Heading**, "- item", paragraphs. */
export function CoachText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) blocks.push(<ul key={`u${blocks.length}`}>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>);
    items = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("- ")) {
      items.push(line.slice(2));
    } else if (/^\*\*.+\*\*$/.test(line)) {
      flush();
      blocks.push(<strong key={`h${blocks.length}`}>{line.slice(2, -2)}</strong>);
    } else {
      flush();
      blocks.push(<p key={`p${blocks.length}`}>{line.replace(/\*\*(.+?)\*\*/g, "$1")}</p>);
    }
  }
  flush();
  return <div className="coach-text">{blocks}</div>;
}

// ------------------------------------------------------------------ icons (stroke, 24px)
const PATHS: Record<string, string> = {
  today: "M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8",
  food: "M7 3v8a2 2 0 0 0 2 2h0a2 2 0 0 0 2-2V3M9 13v8M17 3c-2 0-3 2.5-3 6s1 4 3 4v8M17 3v18",
  train: "M6 7v10M18 7v10M3 10v4M21 10v4M6 12h12",
  body: "M12 21s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.6-7 10-7 10z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  x: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  scan: "M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M7 12h10",
  search: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14M20 20l-4-4",
  chevron: "M9 6l6 6-6 6",
  back: "M15 6l-6 6 6 6",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  check: "M5 12l5 5L20 7",
  disc: "M3 12c0-2.2 4-4 9-4s9 1.8 9 4-4 4-9 4-9-1.8-9-4z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  copy: "M8 8h11v11H8zM5 16V5h11",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
  pulse: "M3 12h4l2-5 4 10 2-5h6",
  inbox: "M4 13l2.5-8h11L20 13v6H4zM4 13h5l1 2h4l1-2h5",
  message: "M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM8.5 9.5h7M8.5 12.5h4",
  run: "M15 3a1.6 1.6 0 1 0 0 3.2a1.6 1.6 0 1 0 0-3.2M8 21l3.2-5.6L14 18v3M11.2 15.4L13 9.5M7.5 11.5l3-2.5h3l2.5 3 2.5.5",
  bolt: "M13 3L5 14h6l-1 7 8-11h-6z",
  flame: "M12 3c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-5 1-8.5z",
  chart: "M4 20V11M10 20V5M16 20v-6M21 20H3",
  gear: "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6M19 12l2-1-1-3-2.2.2-1.3-1.3L16.7 4.7 14 3.6 13 5.5h-2L10 3.6 7.3 4.7l.2 2.2-1.3 1.3L4 8 3 11l2 1-2 1 1 3 2.2-.2 1.3 1.3-.2 2.2L10 20.4l1-1.9h2l1 1.9 2.7-1.1-.2-2.2 1.3-1.3L20 16l1-3z",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  book: "M6 4h11a1 1 0 0 1 1 1v15H7a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1zM5 18a2 2 0 0 1 2-2h11M9 8h5",
  kettlebell: "M9 8.5a3 3 0 1 1 6 0M7.5 8.5h9l1.5 11.5H6z",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
  scale: "M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM8.5 10a3.5 3.5 0 0 1 7 0M12 10l1.5-2.2",
  drop: "M12 3.5c3.2 4 6 7.3 6 10.5a6 6 0 0 1-12 0c0-3.2 2.8-6.5 6-10.5z",
  target: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9M12 11.2a.8.8 0 1 0 0 1.6a.8.8 0 1 0 0-1.6",
};

export function Icon({ name, size = 22 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
