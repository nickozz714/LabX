/**
 * KluisTabsPage — geheimen en Azure-profielen onder één dak.
 *
 * Allebei zijn het inloggegevens die de agent mag gebruiken maar nooit mag
 * zien. Ze stonden als twee losse tabs naast elkaar terwijl je ze om dezelfde
 * reden opzoekt.
 */
import { Outlet } from "react-router-dom";
import { KeyRound, Lock } from "lucide-react";

import { SubTabs } from "@/components/SubTabs";

export function KluisTabsPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <SubTabs tabs={[
        { to: "/kluis/geheimen", label: "Geheimen", icon: Lock },
        { to: "/kluis/azure", label: "Azure-profielen", icon: KeyRound },
      ]} />
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
