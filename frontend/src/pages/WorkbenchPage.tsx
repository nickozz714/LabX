/**
 * WorkbenchPage — Skills & Tools, Workflows en Scheduling bij elkaar.
 *
 * Deze drie horen bij elkaar: het is allemaal "wat kan de agent, en wanneer
 * doet hij het". Als losse tabs vraten ze een kwart van de bovenbalk.
 */
import { Outlet } from "react-router-dom";
import { CalendarClock, Workflow, Wrench } from "lucide-react";

import { SubTabs } from "@/components/SubTabs";

export function WorkbenchPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <SubTabs tabs={[
        { to: "/workbench/skills", label: "Skills & Tools", icon: Wrench },
        { to: "/workbench/workflows", label: "Workflows", icon: Workflow },
        { to: "/workbench/scheduling", label: "Scheduling", icon: CalendarClock },
      ]} />
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
