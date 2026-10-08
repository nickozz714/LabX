/**
 * components/LabAllowlist.tsx
 *
 * Per-lab allowlist: which HOST-located MCP servers/tools and which skills
 * this lab's chat may reach. Ported in spirit from ND3X's LabAllowlist.tsx.
 *
 * This is also the concrete answer to "skills moeten optioneel zijn": a
 * host tool checked here becomes usable from chat immediately — no skill
 * required. Skills (checked separately below) add optional how-to guidance
 * on top, they are never a prerequisite for a tool to work.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { mcpServerApi, skillApi, toolApi } from "@/lib/skills";
import type { MCPServerDto, SkillDto, ToolDto } from "@/lib/types";
import { labsApi } from "@/lib/labs";
import type { Lab } from "@/lib/types";
import { Button } from "@/components/ui";

export function LabAllowlist({ lab, onSaved }: { lab: Lab; onSaved: (updated: Lab) => void }) {
  const [servers, setServers] = useState<MCPServerDto[]>([]);
  const [tools, setTools] = useState<ToolDto[]>([]);
  const [skills, setSkills] = useState<SkillDto[]>([]);
  const [allowMcp, setAllowMcp] = useState<Set<string>>(new Set(lab.allowed_mcp));
  const [allowTools, setAllowTools] = useState<Set<string>>(new Set(lab.allowed_tools));
  // Welke servers je hebt opengeklapt. Dicht is de normale stand.
  const [uitgeklapt, setUitgeklapt] = useState<Set<string>>(new Set());
  const [allowSkills, setAllowSkills] = useState<Set<string>>(new Set(lab.allowed_skills));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    mcpServerApi.list().then((all) => setServers(all.filter((s) => s.location === "host")));
    toolApi.list().then(setTools);
    // Alleen skills die bij een lab kunnen horen. Een skill met "alleen in een
    // sessie" hoort bij het gesprek en gaat sowieso mee — hem hier aanvinken
    // zou suggereren dat het iets uitmaakt.
    skillApi.list().then((alle) =>
      setSkills(alle.filter((x) => (x.usage_scope || "beide") !== "sessie")));
  }, []);

  const alwaysAllowed = servers.filter((s) => s.always_allowed);
  const gatedServers = servers.filter((s) => !s.always_allowed);

  function toggle(set: Set<string>, setter: (s: Set<string>) => void, value: string) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setter(next);
  }

  async function save() {
    setSaving(true);
    try {
      const updated = await labsApi.update(lab.id, {
        allowed_mcp: [...allowMcp],
        allowed_tools: [...allowTools],
        allowed_skills: [...allowSkills],
      });
      onSaved(updated);
    } finally {
      setSaving(false);
    }
  }

  const hostToolsByServer = gatedServers.map((s) => ({
    server: s,
    tools: tools.filter((t) => t.mcp_server?.id === s.id),
  }));

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Standaard is een lab-chat volledig uitgekleed tot alleen de container-shell. Vink hieronder
        expliciet aan welke externe (host-)MCP-servers/tools en skills deze chat ook mag gebruiken.
        Tools werken zonder skill — een skill is optionele how-to, geen vereiste.
      </p>

      {alwaysAllowed.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Altijd beschikbaar in elk lab (ingesteld bij de server, geen keuze hier nodig):{" "}
          {alwaysAllowed.map((s) => s.name).join(", ")}.
        </p>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold">Externe MCP-servers &amp; tools</h3>
        {/* Standaard dicht. Een server met vijftig tools duwt de rest van de
            pagina weg, terwijl je in verreweg de meeste gevallen gewoon de
            hele server aanzet. Uitklappen doe je alleen als je er een paar
            los wilt kiezen. */}
        {hostToolsByServer.length === 0 ? (
          <p className="text-xs text-muted-foreground">Geen externe (host) MCP-servers geregistreerd.</p>
        ) : (
          <div className="space-y-3">
            {hostToolsByServer.map(({ server, tools: serverTools }) => {
              const open = uitgeklapt.has(server.slug);
              const losGekozen = serverTools.filter((t) => allowTools.has(t.name)).length;
              return (
              <div key={server.id} className="rounded-md border border-border p-2">
                <div className="flex items-center gap-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium">
                    <input
                      type="checkbox"
                      checked={allowMcp.has(server.slug)}
                      onChange={() => toggle(allowMcp, setAllowMcp, server.slug)}
                    />
                    <span className="truncate">{server.name}</span>
                    <span className="shrink-0 text-xs font-normal text-muted-foreground">
                      (hele server)
                    </span>
                  </label>
                  {serverTools.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        const n = new Set(uitgeklapt);
                        if (n.has(server.slug)) n.delete(server.slug);
                        else n.add(server.slug);
                        setUitgeklapt(n);
                      }}
                      className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs
                                 text-muted-foreground hover:bg-muted"
                    >
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {serverTools.length} tools
                      {losGekozen > 0 && !allowMcp.has(server.slug) && (
                        <span className="rounded-full bg-primary/15 px-1.5 text-[10px] font-medium text-primary">
                          {losGekozen} gekozen
                        </span>
                      )}
                    </button>
                  )}
                </div>
                {/* Eén zin, op de plek waar je het aanzet. De guard kijkt niet
                    mee bij een host-server, dus wat hier binnenkomt gaat
                    ongefilterd naar het model. Bij metadata-servers is dat
                    onschuldig; bij een server die mail en chats teruggeeft is
                    het de hele afweging, en die hoort niet in een handleiding
                    te staan die je pas achteraf leest. */}
                {server.slug === "work-iq" && (
                  <p className="ml-6 mt-1 text-xs text-yellow-600 dark:text-yellow-500">
                    Aanzetten betekent dat de agent jouw mail, agenda en Teams-gesprekken kan
                    lezen — en die inhoud gaat naar het model. De data-guard kijkt hier niet mee:
                    die bewaakt wat een lab-container uitstuurt, en dit is een host-server.
                    Alleen wat jíj mag zien, want Work IQ werkt namens jou.
                  </p>
                )}
                {open && serverTools.length > 0 && (
                  <div className="ml-6 mt-2 max-h-64 space-y-1 overflow-y-auto border-l border-border pl-3">
                    {serverTools.map((t) => (
                      <label key={t.id} className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={allowTools.has(t.name)}
                          onChange={() => toggle(allowTools, setAllowTools, t.name)}
                          disabled={allowMcp.has(server.slug)}
                        />
                        <span className="font-mono">{t.name}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">Skills (optioneel — how-to, geen vereiste)</h3>
        {skills.length === 0 ? (
          <p className="text-xs text-muted-foreground">Geen skills aangemaakt.</p>
        ) : (
          <div className="space-y-1">
            {skills.map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={allowSkills.has(s.name)}
                  onChange={() => toggle(allowSkills, setAllowSkills, s.name)}
                />
                {s.display_name || s.name}
                {(s.usage_scope || "beide") === "lab" && (
                  <span className="text-[11px] text-muted-foreground"
                        title="Deze skill telt alleen in labs waar hij is aangevinkt">
                    alleen hier
                  </span>
                )}
              </label>
            ))}
          </div>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Leeg = geen extra restrictie: elke ingeschakelde skill levert how-to-guidance. Een skill
          die op <strong>alleen in een lab</strong> staat doet dat niet — die telt pas als hij hier
          is aangevinkt. Skills die op <strong>alleen in een sessie</strong> staan gaan sowieso mee
          en staan daarom niet in deze lijst.
        </p>
      </div>

      <Button onClick={save} disabled={saving}>
        {saving ? "Opslaan…" : "Toegang opslaan"}
      </Button>
    </div>
  );
}
