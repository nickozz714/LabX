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
import type React from "react";
import { Paperclip, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { labsApi, type Bijlage } from "@/lib/labs";
import { Badge } from "@/components/ui";

export function leesbareMaat(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Een screenshot plakken in plaats van opslaan-en-kiezen.
 *
 * Dit is dezelfde weg als de paperclip: het plaatje gaat naar het lab en in
 * je bericht komt het pad. Alleen de aanleiding verschilt, en dat scheelt de
 * drie stappen die je anders zet — opslaan, terugvinden, kiezen — voor iets
 * wat je tien keer per dag doet.
 *
 * Twee dingen die eigen zijn aan het klembord:
 *
 * - **Er is geen bestandsnaam.** Een geplakte afbeelding heet in de browser
 *   vaak "image.png" of niets. Alles zou dus image.png heten en elkaar
 *   overschrijven; daarom een naam met het tijdstip erin.
 * - **Tekst blijft tekst.** Plak je gewone tekst, dan doen we niets en laat
 *   de browser hem gewoon in het veld vallen. Alleen bij een afbeelding
 *   onderscheppen we het.
 */
export function usePlakbijlage({
  labId, dir, onToegevoegd, disabled,
}: {
  labId: string | null | undefined;
  dir: string;
  onToegevoegd: (bijlagen: Bijlage[]) => void;
  disabled?: boolean;
}) {
  const [plakt, setPlakt] = useState(false);
  const [plakfout, setPlakfout] = useState<string | null>(null);

  const onPaste = async (e: React.ClipboardEvent) => {
    if (disabled || !labId) return;
    const plaatjes = Array.from(e.clipboardData?.items || [])
      .filter((i) => i.kind === "file" && i.type.startsWith("image/"))
      .map((i) => i.getAsFile())
      .filter((f): f is File => f !== null);
    if (!plaatjes.length) return;        // gewone tekst: niets doen

    e.preventDefault();
    setPlakt(true);
    setPlakfout(null);
    try {
      const stempel = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const bestanden = plaatjes.map((f, i) => new File(
        [f],
        `schermafbeelding-${stempel}${plaatjes.length > 1 ? `-${i + 1}` : ""}.` +
          (f.type.split("/")[1] || "png"),
        { type: f.type }));
      const r = await labsApi.upload(labId, bestanden, dir);
      if (r.skipped?.length) {
        setPlakfout(r.skipped.map((x) => `${x.name}: ${x.reden}`).join(" · "));
      }
      if (r.files?.length) onToegevoegd(r.files);
    } catch (err) {
      setPlakfout(err instanceof ApiError ? err.message : "Plakken mislukte");
    } finally {
      setPlakt(false);
    }
  };

  return { onPaste, plakt, plakfout };
}

export function BijlageKnop({
  labId,
  dir,
  disabled,
  onToegevoegd,
  compact,
  alleenIcoon,
}: {
  labId: string | null | undefined;
  /** Doelmap in het lab. De aanroeper bepaalt die, zodat bijlagen van een
   *  gesprek en die van een ticket uit elkaar blijven. */
  dir: string;
  disabled?: boolean;
  onToegevoegd: (bijlagen: Bijlage[]) => void;
  compact?: boolean;
  /** Alleen de paperclip, als vierkant knopje. Voor de berichtbalk, waar een
   *  knop met het woord "Bijlage" erop de regel uit elkaar trekt. */
  alleenIcoon?: boolean;
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
        aria-label={alleenIcoon ? "Bijlage toevoegen" : undefined}
        className={`flex items-center gap-1 rounded border border-border text-muted-foreground
          hover:bg-secondary disabled:opacity-40 ${
            alleenIcoon
              ? `size-9 shrink-0 justify-center rounded-lg ${bezig ? "animate-pulse" : ""}`
              : compact ? "px-2 py-1 text-[11px]" : "px-2 py-1.5 text-xs"}`}
      >
        <Paperclip size={alleenIcoon ? 16 : 13} />
        {!alleenIcoon && (bezig ? "Uploaden…" : "Bijlage")}
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
