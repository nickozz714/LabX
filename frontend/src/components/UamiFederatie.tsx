/**
 * components/UamiFederatie.tsx — de managed identity van een klant gebruiken
 * vanaf een server die niet in Azure draait.
 *
 * Waarom dit formulier anders is dan de andere: bij een service principal vul
 * je een geheim in. Hier is er geen geheim — dat is het punt. LabX maakt zelf
 * een sleutelpaar, publiceert de publieke helft, en tekent daarmee een token
 * waarvan de klant heeft vastgelegd dat hij het vertrouwt. Staat dat niet op
 * het scherm, dan ga je zoeken naar een client secret dat niet bestaat.
 *
 * Het tweede deel (`UamiPubliceren`) is het stuk dat je één keer per klant
 * doet: twee bestanden publiceren en bij de klant een federated credential
 * registreren. Alle waarden die daarvoor nodig zijn staan hier met een
 * kopieerknop, want één tekenverschil in de issuer laat de uitwisseling
 * mislukken zonder foutmelding die daarover gaat.
 */
import { useEffect, useState } from "react";
import { Copy, KeyRound, RefreshCw } from "lucide-react";
import { azureProfilesApi, type UamiIssuer } from "@/lib/azureProfiles";
import type { AzureProfileDto } from "@/lib/types";
import { Badge, Button, Input, Label } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";
import { ApiError } from "@/lib/api";

export type UamiConfig = {
  tenant_id: string;
  client_id: string;
  issuer: string;
  subject: string;
  scope: string;
};

export function UamiVelden({ waarde, onChange }: {
  waarde: UamiConfig;
  onChange: (c: UamiConfig) => void;
}) {
  const zet = (bij: Partial<UamiConfig>) => onChange({ ...waarde, ...bij });

  return (
    <div className="space-y-3">
      <div className="space-y-2 rounded-md border border-border bg-secondary/30 p-3 text-xs
                      leading-relaxed">
        <p>
          <strong className="text-foreground">Wat dit is.</strong> Een managed identity hangt
          aan een Azure-resource en is van buitenaf niet op te halen — er is geen client secret
          en geen certificaat. Met <em>workload identity federation</em> draait dat om: bij de
          klant leg je vast dat tokens van <strong>onze</strong> issuer, met een bepaald
          subject, mogen gelden als die identiteit.
        </p>
        <p>
          LabX maakt zelf een sleutelpaar. De publieke helft publiceer je als twee statische
          bestanden op je eigen domein; de privésleutel blijft hier, versleuteld. Bij elke
          tokenaanvraag tekent LabX een verse JWT en wisselt die bij Entra in.
        </p>
        <p className="text-amber-600">
          <strong>Let op:</strong> die privésleutel ís daarmee de identiteit, bij elke klant
          waar hij geregistreerd staat. Hij verlaat de server nooit, en bij twijfel roteer je
          hem — dat kan zonder onderbreking.
        </p>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <div>
          <Label>Tenant-id van de klant</Label>
          <Input placeholder="00000000-0000-0000-0000-000000000000" value={waarde.tenant_id}
                 onChange={(e) => zet({ tenant_id: e.target.value })} className="font-mono" />
        </div>
        <div>
          <Label>Client-id van de managed identity</Label>
          <Input placeholder="00000000-0000-0000-0000-000000000000" value={waarde.client_id}
                 onChange={(e) => zet({ client_id: e.target.value })} className="font-mono" />
          <p className="mt-1 text-[11px] text-amber-600">
            De <strong>client</strong>-id, niet het object- of principal-id. Dat tweede heb je
            nodig voor groepen en workspace-rollen; ze verwisselen geeft een foutmelding
            waarin niets over de oorzaak staat.
          </p>
        </div>
      </div>

      <div>
        <Label>Issuer — het adres waar jouw twee bestanden komen te staan</Label>
        <Input placeholder="https://jouwdomein.nl/oidc" value={waarde.issuer}
               onChange={(e) => zet({ issuer: e.target.value })} className="font-mono" />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Https, publiek bereikbaar voor Microsoft, zonder schuine streep aan het eind. Dit
          adres komt straks <em>letterlijk</em> zo in de federated credential van elke klant;
          één teken verschil laat de uitwisseling mislukken zonder melding die daarover gaat.
        </p>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <div>
          <Label>Subject</Label>
          <Input placeholder="labx" value={waarde.subject}
                 onChange={(e) => zet({ subject: e.target.value })} className="font-mono" />
          <p className="mt-1 text-[11px] text-muted-foreground">
            Wie wij zeggen te zijn. Per klant een eigen waarde maakt achteraf zichtbaar welke
            koppeling er aan de knoppen zat.
          </p>
        </div>
        <div>
          <Label>Waarvoor het token moet gelden</Label>
          <Input placeholder="https://api.fabric.microsoft.com/.default" value={waarde.scope}
                 onChange={(e) => zet({ scope: e.target.value })} className="font-mono" />
        </div>
      </div>

      <div className="rounded-md border border-dashed border-border p-2 text-[11px]
                      text-muted-foreground">
        <strong className="text-foreground">Na het aanmaken</strong> krijg je de twee bestanden
        te zien die je moet publiceren, plus de waarden voor de federated credential bij de
        klant. Pas daarna werkt <em>Controleren</em>.
      </div>
    </div>
  );
}

/** Het stuk dat je één keer per klant doet. */
export function UamiPubliceren({ profiel }: { profiel: AzureProfileDto }) {
  const melding = useMelding();
  const [uit, setUit] = useState<UamiIssuer | null>(null);
  const [bezig, setBezig] = useState(false);
  const [fout, setFout] = useState<string | null>(null);

  function laad() {
    azureProfilesApi.issuerBestanden(profiel.id)
      .then(setUit)
      .catch((e) => setFout(e instanceof ApiError ? e.message : "Ophalen mislukt"));
  }
  useEffect(laad, [profiel.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  function kopieer(tekst: string, wat: string) {
    navigator.clipboard?.writeText(tekst);
    melding.ok(`${wat} gekopieerd`);
  }

  if (fout) return <p className="text-xs text-destructive">{fout}</p>;
  if (!uit) return <p className="text-xs text-muted-foreground">Bezig…</p>;

  const paden = Object.keys(uit.bestanden);

  return (
    <div className="space-y-3 text-xs">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <Label>1 · Publiceer deze twee bestanden</Label>
          <Badge tone="neutral">onder {uit.issuer}</Badge>
        </div>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Statisch, met <code>Content-Type: application/json</code>. Let op dat je webserver
          de map <code>.well-known</code> niet blokkeert met een regel tegen verborgen
          bestanden — dat is de meest gemaakte fout, en de foutmelding van Entra zegt er niets
          over.
        </p>
        <div className="space-y-2">
          {paden.map((pad) => (
            <div key={pad}>
              <div className="mb-1 flex items-center gap-2">
                <code className="font-mono text-[11px]">{uit.issuer}/{pad}</code>
                <Button variant="ghost" className="px-1 text-[11px]"
                        onClick={() => kopieer(
                          JSON.stringify(uit.bestanden[pad], null, 2), pad)}>
                  <Copy size={11} className="mr-1" /> kopieer
                </Button>
              </div>
              <pre className="max-h-48 overflow-auto rounded bg-background p-2 text-[10px]">
                {JSON.stringify(uit.bestanden[pad], null, 2)}
              </pre>
            </div>
          ))}
        </div>
      </div>

      <div>
        <Label>2 · Registreer dit bij de klant op de managed identity</Label>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Azure Portal → de managed identity → <em>Federated credentials</em> → <em>Add</em> →
          scenario <em>Other issuer</em>. Deze drie waarden moeten letterlijk kloppen.
        </p>
        <div className="space-y-1">
          {[["Issuer", uit.issuer], ["Subject identifier", uit.subject],
            ["Audience", uit.audience]].map(([label, waarde]) => (
            <div key={label} className="flex items-center gap-2">
              <span className="w-36 text-muted-foreground">{label}</span>
              <code className="flex-1 truncate rounded bg-background px-2 py-1 font-mono
                               text-[11px]">{waarde}</code>
              <Button variant="ghost" className="px-1"
                      onClick={() => kopieer(String(waarde), label)}>
                <Copy size={11} />
              </Button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <KeyRound size={13} className="text-muted-foreground" />
        <span className="text-muted-foreground">
          sleutel <code className="font-mono">{uit.actieve_kid}</code>
        </span>
        <span className="flex-1" />
        <Button variant="secondary" className="text-[11px]" disabled={bezig}
                title="Zet een nieuwe sleutel ernaast en teken daar voortaan mee"
                onClick={async () => {
                  setBezig(true);
                  try {
                    setUit(await azureProfilesApi.roteerSleutel(profiel.id));
                    melding.ok("Nieuwe sleutel gemaakt — publiceer de jwks.json opnieuw");
                  } finally { setBezig(false); }
                }}>
          <RefreshCw size={11} className="mr-1" /> Sleutel roteren
        </Button>
        <Button variant="ghost" className="text-[11px]" disabled={bezig}
                title="Pas doen als de nieuwe jwks.json gepubliceerd is en het weer werkt"
                onClick={async () => {
                  setBezig(true);
                  try {
                    setUit(await azureProfilesApi.oudeSleutelsWeg(profiel.id));
                    melding.ok("Oude sleutels verwijderd");
                  } finally { setBezig(false); }
                }}>
          Oude sleutels opruimen
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Bij een rotatie blijft de oude sleutel in de <code>jwks.json</code> staan: publiceer
        het nieuwe bestand vóórdat je erop vertrouwt, want Entra haalt de sleutels op wanneer
        het hém uitkomt. Ruim de oude pas op als er weer tokens binnenkomen.
      </p>
    </div>
  );
}
