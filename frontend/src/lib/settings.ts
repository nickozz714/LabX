import { api } from "@/lib/api";
import type { AppSettingsDto } from "@/lib/types";

export const settingsApi = {
  get: () => api.get<AppSettingsDto>("/settings"),
  update: (payload: Record<string, any>) => api.put<AppSettingsDto>("/settings", payload),
};
