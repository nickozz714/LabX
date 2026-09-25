/**
 * components/Foutvanger.tsx — één kapot veld mag niet de hele app wit maken.
 *
 * Aanleiding: de skills-lijst toonde `s.tools.length` terwijl de API dat veld
 * niet meestuurde. Met nul skills viel dat niemand op (die regel draait dan
 * nooit); bij de eerste skill gooide React de hele boom weg en keek je tegen
 * een wit scherm aan. Geen melding, geen aanwijzing, niets — je weet niet eens
 * óf je iets kapot hebt gemaakt.
 *
 * Een foutvanger verandert dat in een leesbaar bericht mét de fout erin. De
 * oorzaak blijft een bug die gefixt moet worden, maar je ziet tenminste wélke,
 * en de rest van de app (de navigatie) blijft staan.
 */
import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type State = { fout: Error | null };

export class Foutvanger extends Component<Props, State> {
  state: State = { fout: null };

  static getDerivedStateFromError(fout: Error): State {
    return { fout };
  }

  componentDidCatch(fout: Error, info: ErrorInfo) {
    // Ook in de console, met de component-stack: dat is wat je nodig hebt om
    // het te vinden, en het bericht op het scherm is voor wie het meldt.
    console.error("Scherm gecrasht:", fout, info.componentStack);
  }

  render() {
    if (!this.state.fout) return this.props.children;
    return (
      <div className="m-6 max-w-2xl rounded-lg border border-destructive/40 bg-destructive/5 p-4">
        <h2 className="text-sm font-semibold">Dit scherm liep vast</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Er ging iets mis bij het tekenen van deze pagina. De rest van LabX werkt gewoon;
          onderstaande melding helpt om het te vinden.
        </p>
        <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-xs">
          {this.state.fout.message || String(this.state.fout)}
        </pre>
        <div className="mt-3 flex gap-2">
          <button className="rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground"
                  onClick={() => this.setState({ fout: null })}>
            Opnieuw proberen
          </button>
          <button className="rounded-md border border-border px-3 py-1.5 text-sm"
                  onClick={() => window.location.reload()}>
            Pagina herladen
          </button>
        </div>
      </div>
    );
  }
}
