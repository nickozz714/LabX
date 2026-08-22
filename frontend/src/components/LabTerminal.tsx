/**
 * components/LabTerminal.tsx — xterm.js over the /labs/{id}/terminal
 * WebSocket, ported in spirit from ND3X's LabTerminal.tsx.
 */
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { labTerminalUrl } from "@/lib/labs";

export function LabTerminal({ labId, token }: { labId: string; token: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({ convertEol: true, fontSize: 13, cursorBlink: true });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();

    const ws = new WebSocket(labTerminalUrl(labId, token));
    ws.onmessage = (ev) => term.write(ev.data);
    ws.onopen = () => fit.fit();
    term.onData((data) => ws.readyState === WebSocket.OPEN && ws.send(data));

    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ resize: [term.cols, term.rows] }));
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      ws.close();
      term.dispose();
    };
  }, [labId, token]);

  return <div ref={containerRef} className="h-80 w-full overflow-hidden rounded bg-black p-1" />;
}
