/**
 * components/LabResources.tsx — wat er in DIT lab te reserveren valt, en wie
 * er nu op zit.
 *
 * Twee dingen op één plek, omdat je ze in dezelfde ademtocht nodig hebt: de
 * keuze welke resources hier gelden, en het zicht op een reservering die vast
 * blijft zitten omdat een agent is afgebroken. Voor dat laatste zit er een
 * knop om hem los te breken — de houdbaarheid vangt het vanzelf op, maar niet
 * altijd snel genoeg als jij nú verder wilt.
 */
import { useEffect, useState } from "react";
import { labsApi } from "@/lib/labs";
import { resourcesApi } from "@/lib/resources";
import type { ActieveClaim, ClaimResourceDto } from "@/lib/resources";
import type { Lab } from "@/lib/types";
import { Badge, Button, Label } from "@/components/ui";

export function LabResources({ lab, onChanged }: { lab: Lab; onChanged: () => void }) {
  const [catalogus, setCatalogus] = useState<ClaimResourceDto[]>([]);
  const [actief, setActief] = useState<ActieveClaim[]>([]);

  function laad() {
    resourcesApi.list().then((r) => setCatalogus(r.filter((x) => x.is_enabled))).catch(() => {});
    resourcesApi.labClaims(lab.id).then((r) => setActief(r.actief)).catch(() => setActief([]));
  }
  useEffect(laad, [lab.id]);

  // null betekent "de standaard", en dat is bewust iets anders dan een lege
  // lijst: een bestaand lab hoort de browser te kunnen reserveren zonder dat
  // iemand eerst een vinkje zet.
  const gekozen = lab.claim_resources;
  const aan = (key: string) =>
    gekozen === null
      ? Boolean(catalogus.find((r) => r.key === key)?.default_on)
      : gekozen.includes(key);

  async function wissel(key: string) {
    const huidig = catalogus.filter((r) => aan(r.key)).map((r) => r.key);
    const nieuw = huidig.includes(key) ? huidig.filter((k) => k !== key) : [...huidig, key];
    await labsApi.update(lab.id, { claim_resources: nieuw });
    onChanged();
  }

  return (
    <div className="space-y-2">
      <Label>Te reserveren in dit lab</Label>
      <p className="text-[11px] text-muted-foreground">
        Draaien er twee sessies in dezelfde werker — bijvoorbeeld twee tickets uit één bundel —
        dan reserveert een agent hiermee wat er maar één keer is. Staat er niets aan, dan kan
        hij niets claimen en moeten ze het onderling uitzoeken.
      </p>
      <div className="flex flex-wrap gap-2">
        {catalogus.map((r) => (
          <button
            key={r.key}
            onClick={() => wissel(r.key)}
            title={r.description || r.label}
            className={`rounded-md border px-2 py-1 text-xs ${
              aan(r.key) ? "border-primary bg-primary/10" : "border-border text-muted-foreground"
            }`}
          >
            {r.label}
          </button>
        ))}
        {catalogus.length === 0 && (
          <span className="text-xs text-muted-foreground">
            Er staat niets in de catalogus (Instellingen → Claimbare resources).
          </span>
        )}
      </div>

      {actief.length > 0 && (
        <div className="space-y-1 rounded-md border border-border bg-secondary/30 p-2">
          <div className="text-[11px] font-medium text-muted-foreground">Nu in gebruik</div>
          {actief.map((c) => (
            <div key={`${c.resource}-${c.houder}`} className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone="yellow">{c.resource}</Badge>
              <span>{c.houder_label || c.houder}</span>
              {c.worker_id && <span className="text-muted-foreground">werker {c.worker_id}</span>}
              <span className="text-muted-foreground">sinds {new Date(c.sinds).toLocaleTimeString()}</span>
              {c.verloopt && (
                <span className="text-muted-foreground">
                  vervalt {new Date(c.verloopt).toLocaleTimeString()}
                </span>
              )}
              <Button variant="ghost"
                      onClick={() => resourcesApi.losbreken(lab.id, c.resource).then(laad)}>
                losbreken
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
