/**
 * components/NetwerkCapaciteit.tsx — wat er in je netwerk nog bij kan.
 *
 * Staat op het Overzicht omdat het daar een beslissing raakt die je anders
 * pas neemt als het te laat is: de server loopt vol, en je weet niet dat er
 * twee meter verderop een machine staat die niets doet. Het antwoord dat dit
 * blok geeft is daarom niet "hoeveel machines heb ik" maar "hoeveel kan er
 * NU nog bij, en waar".
 *
 * Een machine die offline is telt NIET mee in dat totaal. Capaciteit die je
 * niet kunt gebruiken is geen capaciteit — hem meetellen zou precies de
 * verkeerde indruk geven op het moment dat je erop rekent.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Monitor, Plus, WifiOff } from "lucide-react";
import { labHostsApi } from "@/lib/labs";
import type { LabHost } from "@/lib/types";
import { Badge, Card } from "@/components/ui";

function gb(mb: number | null | undefined): string {
  if (!mb) return "—";
  return `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`;
}

export function NetwerkCapaciteit() {
  const [hosts, setHosts] = useState<LabHost[] | null>(null);

  useEffect(() => {
    let gestopt = false;
    const haal = () => labHostsApi.list()
      .then((l) => !gestopt && setHosts(l))
      .catch(() => !gestopt && setHosts([]));
    haal();
    const t = setInterval(haal, 20000);
    return () => { gestopt = true; clearInterval(t); };
  }, []);

  if (!hosts) return null;
  const gekoppeld = hosts.filter((h) => h.status === "gekoppeld");
  const wachtend = hosts.filter((h) => h.status === "nieuw");

  // Niets gekoppeld en niets dat wacht: dan is dit blok ruis op een scherm
  // dat over vandaag gaat.
  if (gekoppeld.length === 0 && wachtend.length === 0) return null;

  const bruikbaar = gekoppeld.filter((h) => h.online);
  const kernen = bruikbaar.reduce((t, h) => t + (h.cpus || 0), 0);
  const vrij = bruikbaar.reduce((t, h) => t + (h.vrij_mb || 0), 0);
  const labs = gekoppeld.reduce((t, h) => t + h.labs.length, 0);
  // Alleen een plafond als je er zelf een hebt gezet; zonder plafond is
  // "hoeveel kan erbij" een vraag die de machine beantwoordt, niet dit getal.
  const metPlafond = bruikbaar.filter((h) => h.max_labs);
  const ruimte = metPlafond.reduce(
    (t, h) => t + Math.max(0, (h.max_labs || 0) - h.labs.length), 0);
  const offline = gekoppeld.length - bruikbaar.length;

  return (
    <Card className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">Machines in je netwerk</span>
        <span className="text-xs text-muted-foreground">
          {bruikbaar.length} van {gekoppeld.length} bereikbaar
          {labs > 0 && ` · ${labs} lab${labs === 1 ? "" : "s"} draait daar`}
        </span>
        {bruikbaar.length > 0 && (
          <Badge tone="green">
            +{kernen} kernen · {gb(vrij)} vrij
          </Badge>
        )}
        {offline > 0 && <Badge tone="neutral">{offline} offline</Badge>}
        <Link to="/labs/machines"
              className="ml-auto text-xs text-muted-foreground underline-offset-2 hover:underline">
          Beheren
        </Link>
      </div>

      {wachtend.length > 0 && (
        <Link to="/labs/machines"
              className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-warning">
          <Plus className="h-3.5 w-3.5 shrink-0" />
          {wachtend.length === 1
            ? `${wachtend[0].naam} heeft zich gemeld en wacht op bevestiging.`
            : `${wachtend.length} computers hebben zich gemeld en wachten op bevestiging.`}
        </Link>
      )}

      {gekoppeld.length > 0 && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {gekoppeld.map((h) => (
            <div key={h.id}
                 className={`rounded-md border border-border p-2 text-xs ${
                   h.online ? "" : "opacity-55"}`}>
              <div className="mb-1 flex items-center gap-1.5">
                {h.online
                  ? <Monitor className="h-3.5 w-3.5 shrink-0 text-success" />
                  : <WifiOff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                <span className="truncate font-medium">{h.naam}</span>
                <span className="ml-auto shrink-0 text-muted-foreground">
                  {h.labs.length}{h.max_labs ? `/${h.max_labs}` : ""} lab
                  {h.labs.length === 1 && !h.max_labs ? "" : "s"}
                </span>
              </div>
              <div className="text-muted-foreground">
                {h.online
                  ? <>{h.cpus ?? "?"} kernen · {gb(h.vrij_mb)} vrij
                      {h.schijf_vrij_gb != null && ` · ${h.schijf_vrij_gb} GB schijf`}</>
                  : "Staat uit of is niet bereikbaar — hier kan nu niets bij."}
              </div>
              {/* Een machine die niet wakker blijft is geen machine waar je een
                  nachtrun op zet, en dat hoort hier te staan en niet pas in het
                  beheerscherm. */}
              {h.online && !h.blijft_wakker && (
                <div className="mt-1 text-warning">Blijft niet wakker.</div>
              )}
              {h.online && (h.schijf_vrij_gb ?? 99) < 15 && (
                <div className="mt-1 text-warning">
                  Weinig schijfruimte — een image ophalen kan hierop stuklopen.
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {bruikbaar.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {metPlafond.length > 0
            ? `Er kunnen nog ${ruimte} lab${ruimte === 1 ? "" : "s"} bij binnen de maxima
               die je zelf hebt gezet.`
            : `Geen maximum ingesteld; wat erbij kan hangt af van wat deze machines
               aankunnen. Zet een maximum bij Labs → Machines als je een grens wilt.`}
          {" "}Een lab kiest zijn machine bij het aanmaken, en je kunt een bestaand
          lab verplaatsen.
        </p>
      )}
    </Card>
  );
}
