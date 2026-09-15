/**
 * components/Bevestiging.tsx — "weet je het zeker?" binnen de app zelf.
 *
 * Aanleiding: verwijderen en "naar de bron sturen" in het ticketpaneel deden
 * niets. Niet bijna niets — er ging geen enkel verzoek naar de server, ook niet
 * in het serverlog. Wat die twee knoppen deelden en de rest niet: ze hingen
 * allebei aan `window.confirm()`.
 *
 * Een browser mág dat dialoogvenster weigeren. Safari doet dat na een paar
 * dialogen op dezelfde pagina ("dit blad geen dialoogvensters meer laten
 * tonen"), en dan geeft `confirm()` gewoon `false` terug — geen venster, geen
 * fout, en de actie stopt stil. Precies het beeld dat gemeld werd. Ook een
 * webview (de desktop-shell) hoeft die functie niet te ondersteunen.
 *
 * Een bevestiging is dus te belangrijk om aan de browser over te laten. Dit
 * venster is van onszelf: het is er altijd, het toont bij welk ding het hoort,
 * en de knop erin is een gewone Button — dus met spinner terwijl het werk
 * loopt. `vraag()` geeft een Promise<boolean>, zodat een aanroep leest als de
 * `confirm()` die hij vervangt:
 *
 *     if (!(await bevestig.vraag({ titel: "Ticket verwijderen?" }))) return;
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui";

export type BevestigVraag = {
  titel: string;
  /** Extra uitleg; hier hoort wat er ONOMKEERBAAR is. */
  tekst?: string;
  /** Tekst op de bevestigknop. */
  bevestig?: string;
  annuleer?: string;
  /** "danger" voor weggooien, "primary" voor de rest. */
  variant?: "danger" | "primary";
};

type BevestigApi = { vraag: (v: BevestigVraag) => Promise<boolean> };

const Ctx = createContext<BevestigApi | null>(null);

/** Zonder provider (bv. in een test) gaat alles door: niet stiekem afbreken. */
export function useBevestiging(): BevestigApi {
  return useContext(Ctx) ?? { vraag: async () => true };
}

export function BevestigingProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<BevestigVraag | null>(null);
  const antwoord = useRef<((ja: boolean) => void) | null>(null);

  const sluit = useCallback((ja: boolean) => {
    antwoord.current?.(ja);
    antwoord.current = null;
    setOpen(null);
  }, []);

  const api = useMemo<BevestigApi>(() => ({
    vraag: (v) =>
      new Promise<boolean>((resolve) => {
        // Een tweede vraag terwijl er al een openstaat: de eerste vervalt met
        // "nee". Blijven hangen op een Promise die niemand meer beantwoordt is
        // erger dan een afgebroken actie.
        antwoord.current?.(false);
        antwoord.current = resolve;
        setOpen(v);
      }),
  }), []);

  useEffect(() => {
    if (!open) return;
    const toets = (e: KeyboardEvent) => {
      if (e.key === "Escape") sluit(false);
      if (e.key === "Enter") sluit(true);
    };
    window.addEventListener("keydown", toets);
    return () => window.removeEventListener("keydown", toets);
  }, [open, sluit]);

  return (
    <Ctx.Provider value={api}>
      {children}
      {open && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
             onClick={() => sluit(false)}>
          <div className="w-full max-w-sm rounded-lg border border-border bg-card p-4 shadow-xl"
               role="alertdialog" aria-modal="true"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start gap-2">
              <AlertTriangle size={16}
                             className={`mt-0.5 shrink-0 ${open.variant === "primary" ? "text-muted-foreground" : "text-destructive"}`} />
              <div className="min-w-0">
                <p className="text-sm font-semibold">{open.titel}</p>
                {open.tekst && (
                  <p className="mt-1 text-xs text-muted-foreground">{open.tekst}</p>
                )}
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" className="text-xs" onClick={() => sluit(false)}>
                {open.annuleer || "Annuleren"}
              </Button>
              <Button autoFocus
                      variant={open.variant === "primary" ? "primary" : "danger"}
                      className="text-xs"
                      onClick={() => sluit(true)}>
                {open.bevestig || "Ja, doorgaan"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
