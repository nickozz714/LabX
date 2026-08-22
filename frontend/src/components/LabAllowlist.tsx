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
  const [allowSkills, setAllowSkills] = useState<Set<string>>(new Set(lab.allowed_skills));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    mcpServerApi.list().then((all) => setServers(all.filter((s) => s.location === "host")));
    toolApi.list().then(setTools);
    skillApi.list().then(setSkills);
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
        {hostToolsByServer.length === 0 ? (
          <p className="text-xs text-muted-foreground">Geen externe (host) MCP-servers geregistreerd.</p>
        ) : (
          <div className="space-y-3">
            {hostToolsByServer.map(({ server, tools: serverTools }) => (
              <div key={server.id} className="rounded-md border border-border p-2">
                <label className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={allowMcp.has(server.slug)}
                    onChange={() => toggle(allowMcp, setAllowMcp, server.slug)}
                  />
                  {server.name}
                  <span className="text-xs font-normal text-muted-foreground">
                    (hele server toestaan — of kies losse tools hieronder)
                  </span>
                </label>
                {serverTools.length > 0 && (
                  <div className="ml-6 mt-1 space-y-1">
                    {serverTools.map((t) => (
                      <label key={t.id} className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={allowTools.has(t.name)}
                          onChange={() => toggle(allowTools, setAllowTools, t.name)}
                          disabled={allowMcp.has(server.slug)}
                        />
                        {t.name}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            ))}
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
              </label>
            ))}
          </div>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Leeg = geen extra restrictie op skills (alle ingeschakelde skills leveren how-to-guidance).
        </p>
      </div>

      <Button onClick={save} disabled={saving}>
        {saving ? "Opslaan…" : "Toegang opslaan"}
      </Button>
    </div>
  );
}
