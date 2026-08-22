import { api } from "@/lib/api";
import type { MCPServerDto, SkillDto, ToolDto } from "@/lib/types";

export const skillApi = {
  list: () => api.get<SkillDto[]>("/skills"),
  get: (id: number) => api.get<SkillDto>(`/skills/${id}`),
  create: (payload: Record<string, any>) => api.post<SkillDto>("/skills", payload),
  update: (id: number, payload: Record<string, any>) => api.patch<SkillDto>(`/skills/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/skills/${id}`),
  setTools: (id: number, tools: { tool_id: number; instructions?: string; is_enabled?: boolean }[]) =>
    api.put<SkillDto>(`/skills/${id}/tools`, { tools }),
};

export const toolApi = {
  list: () => api.get<ToolDto[]>("/tools"),
  get: (id: number) => api.get<ToolDto>(`/tools/${id}`),
  update: (id: number, payload: Record<string, any>) => api.patch<ToolDto>(`/tools/${id}`, payload),
  bulk: (payload: { tool_ids?: number[]; mcp_server_id?: number; is_enabled?: boolean; provenance?: "control" | "data" }) =>
    api.post<{ ok: boolean; updated: number }>("/tools/bulk", payload),
};

export const mcpServerApi = {
  list: () => api.get<MCPServerDto[]>("/mcp-servers"),
  create: (payload: Record<string, any>) => api.post<MCPServerDto>("/mcp-servers", payload),
  update: (id: number, payload: Record<string, any>) => api.patch<MCPServerDto>(`/mcp-servers/${id}`, payload),
  remove: (id: number) => api.delete<{ ok: boolean }>(`/mcp-servers/${id}`),
  sync: (id: number) => api.post<{ ok: boolean; tool_count?: number; error?: string; server: MCPServerDto }>(
    `/mcp-servers/${id}/sync`,
  ),
  catalog: () => api.get<CatalogEntry[]>("/mcp-servers/catalog"),
  installFromCatalog: (key: string) => api.post<MCPServerDto>(`/mcp-servers/catalog/${key}/install`),
};

export interface CatalogEntry {
  key: string;
  name: string;
  description: string;
  kind: "stdio" | "http";
  runner?: "npx" | "uvx";
  package?: string;
  base_url?: string;
  suggested_location: "host" | "lab";
  suggested_args?: string;
  provenance: "control" | "data";
  installed: boolean;
}
