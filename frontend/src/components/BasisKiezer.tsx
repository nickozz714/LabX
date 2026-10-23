/**
 * Welke branch is de basis — dus waarvan deze is afgetakt.
 *
 * Dit is een eigen onderdeel omdat de vraag op twee plekken terugkomt (de
 * pull request en het opsplitsen) en het antwoord er op allebei toe doet:
 * met de verkeerde basis staat alles wat sinds een release op main kwam
 * ineens in jouw diff.
 *
 * Een aanname wordt als aanname getoond. Komt de basis uit een open pull
 * request, dan is het een feit en zeggen we dat erbij; valt LabX terug op de
 * standaardbranch, dan hoor je dat te zien vóórdat je iets maakt.
 */
import { useEffect, useState } from "react";
import { GitBranch } from "lucide-react";

import { clusterApi } from "@/lib/clusters";
import { Label, Select } from "@/components/ui";

export const BasisKiezer = ({
  labId, basis, bron, zeker, onKies,
}: {
  labId: string;
  basis: string;
  bron: string;
  zeker: boolean;
  onKies: (basis: string) => void;
}) => {
  const [takken, setTakken] = useState<string[]>([]);

  useEffect(() => {
    clusterApi.branchLijst(labId)
      .then((l) => setTakken(l.branches.filter((b) => b.op_remote).map((b) => b.naam)))
      .catch(() => setTakken([]));
  }, [labId]);

  const lijst = takken.includes(basis) ? takken : [basis, ...takken];

  return (
    <div className="rounded-md border border-border bg-muted/40 p-2">
      <Label>
        <span className="flex items-center gap-1.5">
          <GitBranch className="h-3 w-3" /> Afgetakt van
        </span>
      </Label>
      <Select value={basis} onChange={(e) => onKies(e.target.value)}>
        {lijst.map((t) => <option key={t} value={t}>{t}</option>)}
      </Select>
      <p className={`mt-1 text-xs ${zeker ? "text-muted-foreground" : "text-warning"}`}>
        {zeker ? `Afgeleid uit ${bron}.` : `Aanname: ${bron}. Klopt dat niet, kies dan
          de juiste — anders komt al het werk van anderen in je diff terecht.`}
      </p>
    </div>
  );
};
