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
  value, onChange, label,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
}) {
  const [profiles, setProfiles] = useState<AzureProfileDto[]>([]);
  useEffect(() => {
    azureProfilesApi.list().then(setProfiles);
  }, []);
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
    </div>
  );
}
