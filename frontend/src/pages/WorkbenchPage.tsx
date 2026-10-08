/**
 * WorkbenchPage — Skills & Tools, Workflows en Scheduling bij elkaar.
 *
 * Deze drie horen bij elkaar: het is allemaal "wat kan de agent, en wanneer
 * doet hij het". Als losse tabs vraten ze een kwart van de bovenbalk.
 */
import { Outlet } from "react-router-dom";
import { CalendarClock, Plug, Sparkles, Workflow, Wrench } from "lucide-react";

import { SubTabs } from "@/components/SubTabs";

export function WorkbenchPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Eén rij. Skills, Tools en MCP-servers hadden hun eigen strip hierónder;
          twee navigaties boven elkaar laat je eerst uitzoeken welke bij welke
          hoort. */}
      <SubTabs tabs={[
        { to: "/workbench/skills", label: "Skills", icon: Sparkles },
        { to: "/workbench/tools", label: "Tools", icon: Wrench },
        { to: "/workbench/mcp", label: "MCP-servers", icon: Plug },
        { to: "/workbench/workflows", label: "Workflows", icon: Workflow },
        { to: "/workbench/scheduling", label: "Scheduling", icon: CalendarClock },
      ]} />
      {/* De strip blijft staan, de inhoud schuift. Zonder dit overflow werd
          alles onder de vouw afgeknipt: de pagina is h-full, en de kinderen
          brengen hun eigen schuifbalk niet mee. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
