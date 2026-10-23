/**
 * De agents die de repository zelf meebrengt.
 *
 * In `.claude/agents/*.md` staan de regels van dát team, voor dát project,
 * door henzelf onderhouden. LabX vond ze niet, om een saaie reden: de
 * Claude-CLI draait op de server en zoekt subagents relatief aan zijn eigen
 * werkmap, terwijl de repo in het lab staat.
 *
 * Die CLI blijft staan waar hij staat. Wat hier gebeurt is dat LabX het
 * bestand uit het lab leest en de inhoud als instructie meegeeft aan een
 * gewone run — dus met het gereedschap van het lab, in de container, en met
 * het spoor, de guards en het afbreken die daar al omheen zitten.
 */
import { useState } from "react";
import { Bot, Loader2, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "@/lib/api";
import { clusterApi } from "@/lib/clusters";
import { Button, Label, TextArea } from "@/components/ui";
import { useMelding } from "@/components/Meldingen";

export const RepoAgents = ({ labId }: { labId: string }) => {
  const melding = useMelding();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<{ naam: string; omschrijving: string }[] | null>(null);
  const [gekozen, setGekozen] = useState("");
  const [opdracht, setOpdracht] = useState("");
  const [bezig, setBezig] = useState(false);
  const [zoekt, setZoekt] = useState(false);

  // GEEN ophalen bij het openen van dit scherm.
  //
  // De lijst komt uit het lab, en dat betekent een wegwerpcontainer starten --
  // bij een lab op een andere machine ook nog over SSH. Dat is seconden werk
  // voor iemand die alleen de Git-knop wilde. Pas als je ernaar vraagt.
  async function zoek() {
    setZoekt(true);
    try {
      setAgents(await clusterApi.repoAgents(labId));
    } catch {
      setAgents([]);
    } finally {
      setZoekt(false);
    }
  }

  if (agents === null) {
    return (
      <div className="flex items-center gap-2">
        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm">Agents uit deze repository</span>
        <Button variant="ghost" className="ml-auto" disabled={zoekt}
                onClick={() => void zoek()}
                title="Kijkt in .claude/agents van de werkkopie in dit lab">
          {zoekt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Zoeken
        </Button>
      </div>
    );
  }
  if (agents.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Deze repository brengt geen agents mee (geen <code>.claude/agents</code>).
      </p>
    );
  }

  async function start() {
    setBezig(true);
    try {
      const uit = await clusterApi.repoAgentStarten(labId, gekozen, opdracht);
      melding.ok(`${uit.agent} gestart`, "Volg hem in de chat van dit lab.");
      navigate(`/chat?thread=${uit.thread_id}`);
    } catch (e) {
      melding.fout("Starten lukte niet",
                   e instanceof ApiError ? e.message : String(e));
    } finally {
      setBezig(false);
    }
  }

  const agent = agents.find((a) => a.naam === gekozen);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-sm">Agents uit deze repository</span>
        <span className="text-xs text-muted-foreground">
          ({agents.length} in .claude/agents)
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {agents.map((a) => (
          <button key={a.naam} type="button"
                  onClick={() => setGekozen(a.naam === gekozen ? "" : a.naam)}
                  className={`rounded border px-2 py-1 font-mono text-xs ${
                    a.naam === gekozen
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-muted"}`}>
            {a.naam}
          </button>
        ))}
      </div>

      {agent && (
        <div className="rounded-md border border-border p-2">
          {agent.omschrijving && (
            <p className="mb-2 text-xs text-muted-foreground">{agent.omschrijving}</p>
          )}
          <Label>Wat moet hij doen?</Label>
          <TextArea rows={3} className="text-xs" value={opdracht}
                    onChange={(e) => setOpdracht(e.target.value)}
                    placeholder="Bijvoorbeeld: splits deze branch op in losse pull requests." />
          <p className="mt-1 text-xs text-muted-foreground">
            Hij draait in dit lab, met het gereedschap van het lab — dezelfde shell,
            git en az als jij. Je volgt hem in de chat en kunt daar doorpraten als hij
            op je akkoord wacht.
          </p>
          <Button variant="primary" className="mt-2" disabled={bezig}
                  onClick={() => void start()}>
            {bezig ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                   : <Play className="h-3.5 w-3.5" />} {agent.naam} starten
          </Button>
        </div>
      )}
    </div>
  );
};
