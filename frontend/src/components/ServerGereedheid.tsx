/**
 * components/ServerGereedheid.tsx — wat moet er nog gebeuren voordat deze
 * MCP-server werkt?
 *
 * Aanleiding: "WorkIQ werkt nog niet echt. Daarnaast zie ik ook nergens de
 * instellingen en wat ik precies moet doen. Daarnaast is bij auth instellen
 * alleen een token." Alle drie terecht, en het waren symptomen van hetzelfde:
 * de onderdelen waren er wel, maar het pad ernaartoe niet.
 *
 * Wat er misging in de praktijk: Work IQ installeren gaf een server in de
 * lijst; "Auth instellen" bood alleen een statisch token (waar die API niets
 * mee doet); de Azure-profielkeuze zat verstopt achter "Verbinding bewerken";
 * en de zes stappen die je in Entra moet zetten stonden wél in de catalogus
 * maar werden nooit uitgelezen. Het enige signaal was een mislukte sync met de
 * tekst "unhandled errors in a TaskGroup (1 sub-exception)".
 *
 * Dit blok zet dat om in een lijst die je van boven naar beneden afloopt.
 */
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CircleAlert } from "lucide-react";
import { mcpServerApi, type Gereedheid } from "@/lib/skills";
import { Badge, Button } from "@/components/ui";

export function ServerGereedheid({ serverId, onNaarProfielen }: {
  serverId: number;
  onNaarProfielen: () => void;
}) {
  const [g, setG] = useState<Gereedheid | null>(null);

  const laad = useCallback(() => {
    mcpServerApi.gereedheid(serverId).then(setG).catch(() => setG(null));
  }, [serverId]);
  useEffect(laad, [laad]);

  // Alleen tonen waar het iets toevoegt: een server zonder scope en zonder
  // problemen heeft hier niets te melden.
  if (!g || (g.klaar && !g.token_scope)) return null;

  return (
    <div className="mt-2 rounded-md border border-border p-2 text-xs">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold">Werkt deze server?</span>
        <Badge tone={g.klaar ? "green" : "yellow"}>{g.klaar ? "klaar" : "nog niet"}</Badge>
        {g.token_scope && (
          <code className="truncate text-[11px] text-muted-foreground">{g.token_scope}</code>
        )}
      </div>

      <ul className="space-y-1">
        {g.punten.map((p, i) => (
          <li key={i} className="flex items-start gap-1.5">
            {p.ok ? <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-500" />
                  : <CircleAlert size={13} className="mt-0.5 shrink-0 text-yellow-600" />}
            <span className="min-w-0">
              <span className={p.ok ? "" : "font-medium"}>{p.titel}</span>
              {p.uitleg && <span className="block text-muted-foreground">{p.uitleg}</span>}
              {p.actie === "azure-profiel" && (
                <Button variant="ghost" className="mt-1 px-2 py-0.5 text-[11px]"
                        onClick={onNaarProfielen}>
                  Naar Azure-profielen
                </Button>
              )}
            </span>
          </li>
        ))}
      </ul>

      {g.setup && (
        <details className="mt-2">
          <summary className="cursor-pointer font-medium text-muted-foreground">
            {g.setup.titel}
          </summary>
          <ol className="mt-1 space-y-1">
            {g.setup.stappen.map((stap, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="font-mono text-muted-foreground">{i + 1}.</span>
                <span className="text-muted-foreground">{stap}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
