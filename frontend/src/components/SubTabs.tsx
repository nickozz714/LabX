/**
 * SubTabs.tsx — het tweede niveau navigatie, binnen een verzamelpagina.
 *
 * De bovenbalk had dertien tabs en dat is er zo'n vijf te veel om nog te
 * kunnen scannen. Verwante schermen staan nu bij elkaar (Workbench, Kluis) met
 * deze strip erbinnen. Bewust visueel lichter dan de hoofdbalk: dit is waar je
 * bént, niet waar je heen gaat.
 */
import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";

export type SubTab = { to: string; label: string; icon: LucideIcon };

export function SubTabs({ tabs }: { tabs: SubTab[] }) {
  return (
    <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-3 py-2 sm:px-6">
      {tabs.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors "
            + (isActive
              ? "bg-secondary text-secondary-foreground"
              : "text-muted-foreground hover:bg-muted")}
        >
          <Icon size={14} />
          {label}
        </NavLink>
      ))}
    </div>
  );
}
