/**
 * contexts/OrchestratorContext.tsx — de spraaksessie leeft boven de pagina.
 *
 * Waarom dit bestaat: de sessie zat in de OrchestratorPage, en React gooit
 * de toestand van een pagina weg zodra je ergens anders heen navigeert. Je
 * kon dus niet even een ticket opzoeken zonder je gesprek kwijt te raken —
 * terwijl juist dát de reden is om een assistent te hebben.
 *
 * Alles wat de sessie is, hangt daarom hier: het sessie-object, de
 * realtime-verbinding, en of de microfoon openstaat. Alles wat het SCHERM is
 * (tekstveld, bezig-vlaggetjes, de orb) blijft in de pagina, want dat hoort
 * wél bij dat ene scherm.
 *
 * EEN OPEN MICROFOON DIE BLIJFT LUISTEREN TERWIJL JE RONDKLIKT is precies
 * waarom dit eerder niet zo was. Dat bezwaar is niet weg, alleen verplaatst:
 * zolang er geluid binnenkomt staat er een duidelijk, pulserend teken in de
 * balk, met "stop" binnen één klik. Zichtbaar laten luisteren is iets anders
 * dan stiekem doorluisteren.
 */
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
} from "react";
import type { ReactNode } from "react";
import { spraakApi } from "@/lib/spraak";
import type { SpraakSessie, SpraakStatus } from "@/lib/spraak";
import { zwijg } from "@/lib/spraak";
import type { RealtimeSessie } from "@/lib/spraakRealtime";

type Ctx = {
  /** De instellingen van de spraakassistent; null zolang ze nog laden. */
  status: SpraakStatus | null;
  herlaadStatus: () => void;
  sessie: SpraakSessie | null;
  setSessie: (s: SpraakSessie | null) => void;
  /** Ophalen én in de context zetten; geeft de verse sessie terug. */
  verversen: (id: string) => Promise<SpraakSessie>;
  /** De realtime-verbinding. Een ref, want hij hoort bij geen enkel scherm. */
  realtime: React.MutableRefObject<RealtimeSessie | null>;
  luistert: boolean;
  setLuistert: (v: boolean) => void;
  rtStatus: string | null;
  setRtStatus: (v: string | null) => void;
  /** Sessie én verbinding afsluiten. */
  stop: () => Promise<void>;
};

const OrchestratorCtx = createContext<Ctx | null>(null);

/** Zonder provider (een test, een los stuk UI) werkt alles behalve de sessie. */
export function useOrchestrator(): Ctx {
  const ctx = useContext(OrchestratorCtx);
  if (!ctx) throw new Error("useOrchestrator buiten OrchestratorProvider");
  return ctx;
}

export function OrchestratorProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SpraakStatus | null>(null);
  const [sessie, setSessie] = useState<SpraakSessie | null>(null);
  const [luistert, setLuistert] = useState(false);
  const [rtStatus, setRtStatus] = useState<string | null>(null);
  const realtime = useRef<RealtimeSessie | null>(null);

  const herlaadStatus = useCallback(() => {
    spraakApi.status().then(setStatus).catch(() => setStatus({ aan: false }));
  }, []);

  useEffect(() => { herlaadStatus(); }, [herlaadStatus]);

  const verversen = useCallback(async (id: string) => {
    const s = await spraakApi.haal(id);
    setSessie(s);
    return s;
  }, []);

  const stop = useCallback(async () => {
    realtime.current?.stop();
    realtime.current = null;
    setLuistert(false);
    setRtStatus(null);
    zwijg();
    const id = sessie?.id;
    setSessie(null);
    if (id) await spraakApi.stop(id).catch(() => {});
  }, [sessie?.id]);

  // Uitloggen of het tabblad sluiten: dan gaat de microfoon hoe dan ook uit.
  // Dit is de enige plek waar dat nog kan, want de pagina ruimt niet meer op.
  useEffect(() => {
    const bij = () => realtime.current?.stop();
    window.addEventListener("pagehide", bij);
    return () => {
      window.removeEventListener("pagehide", bij);
      realtime.current?.stop();
      realtime.current = null;
    };
  }, []);

  return (
    <OrchestratorCtx.Provider value={{
      status, herlaadStatus, sessie, setSessie, verversen,
      realtime, luistert, setLuistert, rtStatus, setRtStatus, stop,
    }}>
      {children}
    </OrchestratorCtx.Provider>
  );
}
