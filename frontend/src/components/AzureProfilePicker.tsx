/**
 * components/AzureProfilePicker.tsx — a plain <select> over the Azure
 * profiles list, reused wherever something needs to be told "authenticate
 * as this Azure identity": a lab (Chat/ChatPage → the lab's own settings)
 * and a host MCP server's own default (SkillsPage → MCP-servers). See
 * services/azure/azure_mcp_auth.py on the backend for how the choice here
 * turns into a live token or an isolated az-CLI session.
 */
import { useEffect, useState } from "react";
import { azureProfilesApi } from "@/lib/azureProfiles";
import type { AzureProfileDto } from "@/lib/types";

export function AzureProfilePicker({
  value, onChange, label, tokenScope,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
  /** Voor welke API deze keuze een token moet opleveren. Is die gezet, dan kan
   *  niet elk profieltype dit bedienen — en dat hoort te blijken op het moment
   *  van kiezen, niet pas uit een mislukte sync. */
  tokenScope?: string | null;
}) {
  const [profiles, setProfiles] = useState<AzureProfileDto[]>([]);
  useEffect(() => {
    azureProfilesApi.list().then(setProfiles);
  }, []);
  const gekozen = profiles.find((p) => p.id === value);
  // Een az-CLI-sessie geeft alleen tokens aan de Azure CLI zelf, en Microsoft
  // autoriseert die niet voor een API als Work IQ (AADSTS65002). Een geplakt
  // bearer-token kan zichzelf niet verversen. Blijft over: een eigen
  // app-registratie, of een service principal als de API dat kent.
  const pastNiet = Boolean(tokenScope && gekozen && gekozen.kind === "msal_bundle");
  return (
    <div>
      {label && <label className="mb-1 block text-xs font-semibold text-muted-foreground">{label}</label>}
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-ring"
      >
        <option value="">Geen (statisch token / lokale az-sessie)</option>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} ({p.kind})
          </option>
        ))}
      </select>
      {pastNiet && (
        <p className="mt-1 rounded-md border border-yellow-500/40 bg-yellow-500/10 p-2 text-xs">
          <span className="font-medium">Dit profiel kan hier geen token voor maken.</span>{" "}
          Een <code>msal_bundle</code> is een az-CLI-sessie, en Microsoft autoriseert de Azure CLI
          niet voor <code>{tokenScope}</code> (AADSTS65002). Maak op de Azure-profielen-pagina een
          profiel van het type <strong>Entra-app</strong> aan — dat logt met een device-code in op
          je eigen app-registratie.
        </p>
      )}
    </div>
  );
}
