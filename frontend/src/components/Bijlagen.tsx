/**
 * components/Bijlagen.tsx
 *
 * Bestanden meesturen met een chatbericht of met de instructie van een
 * agent-run.
 *
 * Het belangrijkste om te weten: een bijlage gaat NIET mee in de tekst. Hij
 * wordt eerst naar het lab geüpload en wat er in het bericht komt is het pad.
 * Dat is geen omweg maar de bedoeling — de agent werkt in dat lab en kan een
 * spreadsheet, een PDF of een screenshot daar openen met het gereedschap dat
 * erbij hoort. In een prompt proppen zou alleen voor kleine tekstbestanden
 * werken, en dan nog kost het de context die je aan het werk wilt besteden.
 *
 * Gevolg voor de gebruiker: uploaden gebeurt op het moment van kiezen, niet
 * bij versturen. Kies je een bestand en bedenk je je, dan staat het al in het
 * lab. Het uit de lijst halen haalt het dus uit je bericht, niet van schijf —
 * de tekst onder de knop zegt dat ook.
 */
import { useRef, useState } from "react";
import { Paperclip, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { labsApi, type Bijlage } from "@/lib/labs";
import { Badge } from "@/components/ui";

export function leesbareMaat(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function BijlageKnop({
  labId,
  dir,
  disabled,
  onToegevoegd,
  compact,
}: {
  labId: string | null | undefined;
  /** Doelmap in het lab. De aanroeper bepaalt die, zodat bijlagen van een
   *  gesprek en die van een ticket uit elkaar blijven. */
  dir: string;
  disabled?: boolean;
  onToegevoegd: (bijlagen: Bijlage[]) => void;
  compact?: boolean;
}) {
  const invoer = useRef<HTMLInputElement>(null);
  const [bezig, setBezig] = useState(false);
  const [fout, setFout] = useState<string | null>(null);

  async function kies(lijst: FileList | null) {
    const files = Array.from(lijst || []);
    if (!files.length || !labId) return;
    setBezig(true);
    setFout(null);
    try {
      const r = await labsApi.upload(labId, files, dir);
      if (r.skipped?.length) {
        setFout(r.skipped.map((s) => `${s.name}: ${s.reden}`).join(" · "));
      }
      if (r.files?.length) onToegevoegd(r.files);
    } catch (err) {
      setFout(err instanceof ApiError ? err.message : "Uploaden mislukt");
    } finally {
      setBezig(false);
      if (invoer.current) invoer.current.value = "";
    }
  }

  const uit = disabled || !labId || bezig;
  return (
    <>
      <button
        type="button"
        disabled={uit}
        onClick={() => invoer.current?.click()}
        title={labId ? "Bestanden meesturen — ze worden in het lab gezet" : "Koppel eerst een lab"}
        className={`flex items-center gap-1 rounded border border-border text-muted-foreground
          hover:bg-secondary disabled:opacity-40 ${compact ? "px-2 py-1 text-[11px]" : "px-2 py-1.5 text-xs"}`}
      >
        <Paperclip size={13} />
        {bezig ? "Uploaden…" : "Bijlage"}
      </button>
      <input ref={invoer} type="file" multiple hidden
             onChange={(e) => kies(e.target.files)} />
      {fout && <span className="text-[11px] text-destructive">{fout}</span>}
    </>
  );
}

export function BijlageLijst({
  bijlagen,
  onVerwijder,
}: {
  bijlagen: Bijlage[];
  onVerwijder: (path: string) => void;
}) {
  if (!bijlagen.length) return null;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1">
      {bijlagen.map((b) => (
        <span key={b.path}
              className="flex items-center gap-1 rounded bg-secondary px-2 py-0.5 text-[11px]"
              title={`${b.path} — staat in het lab`}>
          <Paperclip size={11} />
          {b.name}
          <span className="text-muted-foreground">{leesbareMaat(b.bytes)}</span>
          <button type="button" onClick={() => onVerwijder(b.path)}
                  title="Niet meesturen (het bestand blijft in het lab staan)">
            <X size={11} />
          </button>
        </span>
      ))}
      <Badge tone="neutral">in het lab</Badge>
    </div>
  );
}
