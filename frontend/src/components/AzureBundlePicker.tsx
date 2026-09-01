/**
 * components/AzureBundlePicker.tsx — de bestanden van een az-sessie kiezen.
 *
 * Een msal_bundle bestaat uit de JSON-bestanden die `az login` in ~/.azure
 * achterlaat. Die met de hand openen, selecteren en in een tekstvak plakken is
 * precies het soort werk waar een bestandskiezer voor bestaat — en plakken gaat
 * bovendien mis (een half gekopieerde token-cache is geldige JSON noch een
 * werkende sessie). Vandaar: kies de bestanden, of de hele map, en de browser
 * leest ze in. Plakken blijft mogelijk voor wie de bestanden alleen als tekst
 * heeft (via een terminal op een andere machine bijvoorbeeld).
 */
import { useRef, useState } from "react";
import { AZURE_BUNDLE_FILES } from "@/lib/azureProfiles";
import { Button, Label, TextArea } from "@/components/ui";
import { FolderOpen, Upload } from "lucide-react";

const REQUIRED = ["msal_token_cache.json", "azureProfile.json"];

export function AzureBundlePicker({
  files, onChange,
}: {
  files: Record<string, string>;
  onChange: (files: Record<string, string>) => void;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const dirRef = useRef<HTMLInputElement | null>(null);

  async function take(list: FileList | null) {
    if (!list || list.length === 0) return;
    const next: Record<string, string> = { ...files };
    const taken: string[] = [];
    const skipped: string[] = [];
    for (const file of Array.from(list)) {
      // Bij een mapkeuze komt élk bestand uit ~/.azure mee; alleen de drie die
      // een bundel vormen zijn interessant, de rest (logs, config) niet.
      const known = AZURE_BUNDLE_FILES.find((n) => n.toLowerCase() === file.name.toLowerCase());
      if (!known) {
        skipped.push(file.name);
        continue;
      }
      try {
        next[known] = await file.text();
        taken.push(known);
      } catch {
        skipped.push(file.name);
      }
    }
    onChange(next);
    setNote(
      taken.length
        ? `Ingelezen: ${taken.join(", ")}${skipped.length ? ` — ${skipped.length} ander(e) bestand(en) overgeslagen` : ""}`
        : "Geen az-bestanden gevonden in wat je koos. Verwacht: " + AZURE_BUNDLE_FILES.join(", "),
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" className="text-xs" onClick={() => fileRef.current?.click()}>
          <Upload size={13} className="mr-1 inline" />
          Bestanden kiezen
        </Button>
        <Button variant="secondary" className="text-xs" onClick={() => dirRef.current?.click()}>
          <FolderOpen size={13} className="mr-1 inline" />
          Hele .azure-map kiezen
        </Button>
        <Button variant="ghost" className="text-xs" onClick={() => setPasting(!pasting)}>
          {pasting ? "Verberg plakvelden" : "Of JSON plakken"}
        </Button>
      </div>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept=".json,application/json"
        className="hidden"
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={dirRef}
        type="file"
        className="hidden"
        // Mapkeuze kent React niet als prop; het is een attribuut van het
        // element zelf en werkt in Chrome, Edge en Safari.
        {...({ webkitdirectory: "", directory: "" } as any)}
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />

      <p className="text-xs text-muted-foreground">
        De bestanden staan in <code>~/.azure</code> (Windows: <code>%USERPROFILE%\.azure</code>). Op
        macOS toont het keuzevenster verborgen mappen met ⌘⇧. — of typ ⌘⇧G en plak het pad.
      </p>

      <ul className="space-y-1 text-xs">
        {AZURE_BUNDLE_FILES.map((name) => {
          const content = files[name];
          const required = REQUIRED.includes(name);
          return (
            <li key={name} className="flex items-center gap-2">
              <span className={content ? "text-success" : required ? "text-destructive" : "text-muted-foreground"}>
                {content ? "✓" : required ? "✗" : "–"}
              </span>
              <span className="font-mono">{name}</span>
              <span className="text-muted-foreground">
                {content
                  ? `${(content.length / 1024).toFixed(1)} kB`
                  : required
                    ? "verplicht"
                    : "optioneel"}
              </span>
              {content && (
                <button
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => {
                    const next = { ...files };
                    delete next[name];
                    onChange(next);
                  }}
                >
                  ✕
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}

      {pasting && (
        <div className="space-y-2 rounded-md border border-border p-2">
          {AZURE_BUNDLE_FILES.map((name) => (
            <div key={name}>
              <Label>{name}</Label>
              <TextArea
                rows={2}
                className="font-mono text-xs"
                value={files[name] || ""}
                onChange={(e) => onChange({ ...files, [name]: e.target.value })}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Of deze bundel compleet genoeg is om op te slaan. */
export function bundleComplete(files: Record<string, string>): boolean {
  return REQUIRED.every((n) => (files[n] || "").trim().length > 0);
}
