/**
 * components/EntraAppLogin.tsx — inloggen bij je eigen Entra-app met een
 * device-code, plus de uitleg van wat daarvoor in Azure geregeld moet zijn.
 *
 * Die uitleg staat hier en niet in een handleiding, omdat dit precies de plek
 * is waar je erachter komt dat er iets mist. Een `AADSTS65001` zegt "geen
 * toestemming gegeven" en `AADSTS7000218` zegt "openbare clientstromen staan
 * uit" — beide zijn in twee klikken op te lossen, maar alleen als je weet
 * wélke twee. Daarom staat de checklist naast de knop en geven we de melding
 * van Entra onverkort door in plaats van er "inloggen mislukt" van te maken.
 */
import { useEffect, useRef, useState } from "react";
import { azureProfilesApi, type DeviceLoginStart } from "@/lib/azureProfiles";
import type { AzureProfileDto } from "@/lib/types";
import { Badge, Button, Card } from "@/components/ui";

/** Wat er in Entra en het M365-beheercentrum moet staan. In de volgorde
 *  waarin je het doet, met per stap waarom — een lijst zonder het waarom
 *  overleeft de eerste foutmelding niet. */
export const ENTRA_STAPPEN: { titel: string; uitleg: string }[] = [
  {
    titel: "Registreer een app in Microsoft Entra",
    uitleg: "Entra → App-registraties → Nieuwe registratie. Accounttype: alleen accounts in " +
            "deze organisatiemap. Een redirect-URI heb je niet nodig.",
  },
  {
    titel: "Zet 'Openbare clientstromen toestaan' op JA",
    uitleg: "Onder Verificatie. Dit is de stap die iedereen overslaat; zonder dit weigert Entra " +
            "de device-code-login met AADSTS7000218.",
  },
  {
    titel: "Voeg de gedelegeerde machtigingen toe",
    uitleg: "API-machtigingen → API's die mijn organisatie gebruikt → Work IQ → Gedelegeerde " +
            "machtigingen → WorkIQAgent.Ask. Application-machtigingen bestaan hier niet: Work IQ " +
            "werkt altijd namens een ingelogd persoon, en daarom ziet de agent precies wat jij " +
            "ziet en niet meer.",
  },
  {
    titel: "Laat een beheerder toestemming verlenen",
    uitleg: "Knop 'Beheerderstoestemming verlenen' op dezelfde pagina. Verplicht voor " +
            "WorkIQAgent.Ask. Ben je zelf geen beheerder, dan is dit het enige waar je iemand " +
            "voor nodig hebt.",
  },
  {
    titel: "Zet Work IQ aan voor de tenant",
    uitleg: "In het Microsoft 365-beheercentrum. Zet daar ook SCHRIJFACTIES aan als de agent " +
            "mail mag versturen of afspraken mag maken — Work IQ staat standaard op alleen-lezen.",
  },
  {
    titel: "Maak een spending policy",
    uitleg: "Work IQ rekent af in Copilot Credits, los van je Microsoft 365 Copilot-licenties. " +
            "Zonder policy kan het gebruik ongemerkt oplopen.",
  },
];

export function EntraStappen() {
  return (
    <details className="rounded-md border border-border p-3 text-xs">
      <summary className="cursor-pointer font-medium">
        Wat moet er eenmalig in Azure geregeld zijn?
      </summary>
      <ol className="mt-2 space-y-2">
        {ENTRA_STAPPEN.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="font-mono text-muted-foreground">{i + 1}.</span>
            <span>
              <span className="font-medium">{s.titel}</span>
              <span className="block text-muted-foreground">{s.uitleg}</span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function EntraAppLogin({ profiel, onKlaar }: {
  profiel: AzureProfileDto; onKlaar: () => void;
}) {
  const [start, setStart] = useState<DeviceLoginStart | null>(null);
  const [fout, setFout] = useState<string | null>(null);
  const [bezig, setBezig] = useState(false);
  const [test, setTest] = useState<string | null>(null);
  const stop = useRef(false);

  useEffect(() => () => { stop.current = true; }, []);

  async function inloggen() {
    setFout(null);
    setBezig(true);
    try {
      const s = await azureProfilesApi.deviceLoginStart(profiel.id);
      setStart(s);
      // Pollen doet de browser, met het interval dat Entra zelf opgeeft:
      // sneller vragen levert een `slow_down` op en vertraagt het juist.
      const eind = Date.now() + s.expires_in * 1000;
      while (!stop.current && Date.now() < eind) {
        await new Promise((r) => setTimeout(r, Math.max(2, s.interval) * 1000));
        const p = await azureProfilesApi.deviceLoginPoll(profiel.id, s.device_code);
        if (p.status === "klaar") {
          setStart(null);
          onKlaar();
          return;
        }
      }
      setFout("De code is verlopen. Begin opnieuw.");
    } catch (err: any) {
      // De melding van Entra zelf, onverkort: daar staat de AADSTS-code in en
      // die wijst precies aan wat er in de app-registratie nog mist.
      setFout(String(err?.message || err));
      setStart(null);
    } finally {
      setBezig(false);
    }
  }

  async function tokenTest() {
    setTest("bezig…");
    try {
      const uit = await azureProfilesApi.tokenTest(
        profiel.id, "api://workiq.svc.cloud.microsoft/.default");
      setTest(uit.ok
        ? `Token gekregen voor ${uit.audience} als ${uit.upn || "onbekend"}.`
        : `Geen token: ${uit.error}`);
    } catch (err: any) {
      setTest(String(err?.message || err));
    }
  }

  return (
    <Card className="space-y-3 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{profiel.name}</span>
        <Badge tone={profiel.logged_in ? "green" : "yellow"}>
          {profiel.logged_in ? "ingelogd" : "nog niet ingelogd"}
        </Badge>
        {profiel.client_id && (
          <code className="text-[11px] text-muted-foreground">app {profiel.client_id}</code>
        )}
      </div>

      {start ? (
        <div className="rounded-md border border-primary/40 bg-primary/5 p-3">
          <p className="text-xs text-muted-foreground">
            Ga naar <a className="underline" href={start.verification_uri} target="_blank"
                       rel="noreferrer">{start.verification_uri}</a> en voer deze code in:
          </p>
          <p className="my-2 select-all font-mono text-2xl tracking-widest">{start.user_code}</p>
          <p className="text-xs text-muted-foreground">
            Dit scherm merkt vanzelf wanneer je klaar bent. De code verloopt na{" "}
            {Math.round(start.expires_in / 60)} minuten.
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabled={bezig} onClick={inloggen}>
            {bezig ? "Bezig…" : profiel.logged_in ? "Opnieuw inloggen" : "Inloggen"}
          </Button>
          {profiel.logged_in && (
            <Button variant="secondary" onClick={tokenTest}
                    title="Haalt echt een token op voor Work IQ — dit is de test die aantoont dat de machtigingen kloppen">
              Work IQ testen
            </Button>
          )}
        </div>
      )}

      {test && <p className="text-xs text-muted-foreground">{test}</p>}
      {fout && (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
          {fout}
        </p>
      )}
      <EntraStappen />
    </Card>
  );
}
