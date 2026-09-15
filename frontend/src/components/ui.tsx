/**
 * components/ui.tsx
 *
 * Minimal, hand-rolled UI primitives styled with Tailwind utility classes on
 * top of the shared design tokens in index.css — the same shadcn-style HSL
 * variable names and blue/violet accent scale ND3X's frontend uses, so LabX
 * reads as the same product family (deliberately without ND3X's bespoke
 * desktop-window-manager chrome, which is a distinct, much larger subsystem
 * — see index.css for that call).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, MouseEvent, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { meldExtern } from "@/components/Meldingen";

/**
 * Een knop die zelf ziet dat hij bezig is.
 *
 * Dit zit hier en niet in elke aanroeper, en dat is de hele truc: geeft een
 * `onClick` een Promise terug — en dat doet elke async handler vanzelf — dan
 * zet deze knop zichzelf op bezig, toont een spinner en weigert een tweede
 * klik tot het klaar is. Alle bestaande knoppen erven dat zonder dat er één
 * aanroep hoeft te veranderen.
 *
 * De aanleiding: "vaak klik ik op een knop en dan lijkt het alsof er niets
 * gebeurt, maar naderhand verandert er wel wat." Een MCP-sync duurt tientallen
 * seconden en gaf ondertussen geen enkel teken van leven. Wie niets ziet
 * gebeuren klikt nog eens — en start de sync dus twee keer.
 *
 * Een mislukte actie meldt zichzelf ook: zonder dat verdwijnt een fout in de
 * console en blijft het scherm doen alsof er niets aan de hand is. Wie dat niet
 * wil (omdat de aanroeper de fout zelf netjes toont) zet `meldFouten={false}`.
 */
export function Button({
  variant = "primary",
  className = "",
  busy,
  busyLabel,
  meldFouten = true,
  onClick,
  children,
  disabled,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  /** Handmatig bezig zetten, voor werk dat buiten deze knop begint. */
  busy?: boolean;
  /** Tekst tijdens het wachten; leeg = de gewone inhoud blijft staan. */
  busyLabel?: string;
  meldFouten?: boolean;
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void | Promise<unknown>;
}) {
  const [zelfBezig, setZelfBezig] = useState(false);
  // Bij een klik die de knop uit beeld haalt (verwijderen, een modal die
  // sluit) mag er geen state meer gezet worden op iets dat er niet meer is.
  const levend = useRef(true);
  useEffect(() => () => { levend.current = false; }, []);

  const klik = useCallback((e: MouseEvent<HTMLButtonElement>) => {
    const uit = onClick?.(e);
    if (!uit || typeof (uit as Promise<unknown>).then !== "function") return;
    setZelfBezig(true);
    (uit as Promise<unknown>)
      .catch((err: unknown) => {
        if (meldFouten) {
          const tekst = err instanceof Error ? err.message : String(err);
          meldExtern("fout", "Dat is niet gelukt", tekst.slice(0, 300));
        }
      })
      .finally(() => { if (levend.current) setZelfBezig(false); });
  }, [onClick, meldFouten]);

  const bezig = busy || zelfBezig;
  const base = "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const variants: Record<string, string> = {
    primary: "bg-primary text-primary-foreground hover:opacity-90",
    secondary: "border border-border bg-card text-foreground hover:bg-secondary",
    danger: "bg-destructive text-destructive-foreground hover:opacity-90",
    ghost: "bg-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
  };
  return (
    <button className={`${base} ${variants[variant]} ${className}`}
            onClick={klik} disabled={disabled || bezig} aria-busy={bezig} {...props}>
      {bezig && <Loader2 size={13} className="animate-spin" />}
      {bezig && busyLabel ? busyLabel : children}
    </button>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-ring ${props.className || ""}`}
    />
  );
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-ring ${props.className || ""}`}
    />
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-ring ${props.className || ""}`}
    />
  );
}

export function Card({ children, className = "", ...props }: HTMLAttributes<HTMLDivElement> & { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-card text-card-foreground shadow-sm ${className}`} {...props}>
      {children}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "red" | "yellow" | "violet" }) {
  const tones: Record<string, string> = {
    neutral: "bg-secondary text-secondary-foreground",
    green: "bg-success/15 text-success",
    red: "bg-destructive/15 text-destructive",
    yellow: "bg-warning/15 text-warning",
    violet: "bg-accent/15 text-accent",
  };
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${tones[tone]}`}>{children}</span>;
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="inline-flex items-center gap-2 cursor-pointer select-none text-sm">
      <span
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${checked ? "bg-primary" : "bg-secondary"}`}
      >
        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-background transition ${checked ? "translate-x-4.5 ml-1" : "translate-x-1"}`} />
      </span>
      {label && <span>{label}</span>}
    </label>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4" onClick={onClose}>
      <div
        className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} max-h-[85vh] overflow-y-auto rounded-xl border border-border bg-card p-5 text-card-foreground shadow-2xl`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return <label className="mb-1 block text-xs font-semibold text-muted-foreground">{children}</label>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">{children}</div>;
}
