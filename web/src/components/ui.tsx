import { useQuery } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
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
  return (
    <div className="stepper">
      <button type="button" onClick={() => onChange(clamp(value - step))} aria-label={`Less ${label}`}>−</button>
      <input
        inputMode="decimal" aria-label={label} value={Number.isFinite(value) ? value.toFixed(digits) : ""}
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          if (!Number.isNaN(v)) onChange(Math.min(max, Math.max(min, v)));
        }}
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

export function Bar({ value, target, max }: { value: number; target: number | null; max?: number | null }) {
  const denom = target || max || 1;
  const pct = Math.min(100, (value / denom) * 100);
  const over = max != null && value > max;
  return (
    <div className="bar" aria-hidden>
      <span className={over ? "over" : ""} style={{ width: `${pct}%` }} />
    </div>
  );
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
