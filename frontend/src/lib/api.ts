/**
 * lib/api.ts
 *
 * Thin fetch wrapper for the LabX API. Token lives in sessionStorage (same
 * pattern as ND3X's AuthContext) and is attached to every request. In dev,
 * Vite proxies /api to the backend (vite.config.ts); in production the
 * built assets are served by nginx which proxies /api the same way (see
 * frontend/nginx.conf).
 */

import { onthoudBestemming } from "@/lib/concept";

const TOKEN_KEY = "labx_access_token";

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Het antwoord als JSON, of een bruikbare fout.
 *
 * `JSON.parse` stond hier kaal, en dat gaf precies de verkeerde melding op
 * het verkeerde moment. Een backend die onderuitgaat antwoordt met platte
 * tekst ("Internal Server Error"), een proxy met HTML; de gebruiker zag dan
 * "Unexpected token I in JSON" — een parse-fout die niets zegt over wat er
 * misging en alles verbergt wat de server wél vertelde.
 *
 * Bij een geslaagd antwoord dat geen JSON is, is het alsnog een fout: de
 * aanroeper verwacht een object.
 */
function leesJson(tekst: string, status: number, statusTekst: string): any {
  if (!tekst) return null;
  try {
    return JSON.parse(tekst);
  } catch {
    // Geen JSON. Dan is de ruwe tekst het beste spoor dat we hebben —
    // afgekapt, want een HTML-pagina hoort niet in een melding.
    const kaal = tekst.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    throw new ApiError(status, kaal.slice(0, 300)
      || statusTekst
      || `De server antwoordde met status ${status} en geen leesbare inhoud.`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    setToken(null);
    // Waar je was gaat mee. Terugkomen op /overzicht nadat je uit een
    // halfafgemaakt plan bent gegooid, betekent zelf terugzoeken waar je
    // gebleven was -- en dat is precies het werk dat je kwijt was.
    onthoudBestemming(window.location.pathname + window.location.search);
    window.location.href = "/login";
    throw new ApiError(401, "Niet ingelogd");
  }
  const text = await res.text();
  const data = leesJson(text, res.status, res.statusText);
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

/**
 * Multipart upload. Bewust NIET via `request`: die zet altijd een JSON
 * content-type, en bij een FormData moet de browser die header zelf zetten —
 * inclusief de `boundary=` die hij genereert. Zet je hem zelf, dan kan de
 * server de delen niet meer scheiden en komt er een lege upload aan.
 */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    setToken(null);
    // Waar je was gaat mee. Terugkomen op /overzicht nadat je uit een
    // halfafgemaakt plan bent gegooid, betekent zelf terugzoeken waar je
    // gebleven was -- en dat is precies het werk dat je kwijt was.
    onthoudBestemming(window.location.pathname + window.location.search);
    window.location.href = "/login";
    throw new ApiError(401, "Niet ingelogd");
  }
  const text = await res.text();
  const data = leesJson(text, res.status, res.statusText);
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

/**
 * Een bestand ophalen en als download aanbieden. Bewust NIET via `request`:
 * die parseert altijd JSON, en een shellscript is dat niet.
 *
 * De servernaam uit `Content-Disposition` wint van een naam die wij
 * verzinnen: de server weet welke branch en welke poort erin zitten, en die
 * horen in de bestandsnaam terug te komen.
 *
 * Geeft de meegestuurde kopregels terug, zodat de aanroeper bijvoorbeeld kan
 * zeggen welk adres je straks moet openen.
 */
async function download(path: string, valnaam: string): Promise<Headers> {
  const res = await fetch(`/api${path}`, { headers: authHeaders() });
  if (res.status === 401) {
    setToken(null);
    onthoudBestemming(window.location.pathname + window.location.search);
    window.location.href = "/login";
    throw new ApiError(401, "Niet ingelogd");
  }
  if (!res.ok) {
    const tekst = await res.text();
    let detail: string = res.statusText;
    try {
      const data = tekst ? JSON.parse(tekst) : null;
      detail = (data && (data.detail || data.error)) || detail;
    } catch {
      /* geen JSON: dan is de statustekst het beste dat we hebben */
    }
    throw new ApiError(res.status, detail);
  }

  const opgegeven = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = opgegeven?.[1] || valnaam;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Pas vrijgeven nadat de browser de klik heeft afgehandeld; meteen
  // intrekken levert soms een leeg bestand op.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return res.headers;
}

export const api = {
  download,
  get: <T>(path: string) => request<T>("GET", path),
  upload,
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Build a same-origin ws:// or wss:// URL for a backend path (terminal, SSE-over-ws if ever needed). */
export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api${path}`;
}

/**
 * Stream Server-Sent Events from a POST endpoint (fetch doesn't support SSE
 * natively for POST, so this reads the response body as a stream and parses
 * `data: {...}\n\n` frames manually).
 */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (data: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch {
          /* ignore a malformed frame */
        }
      }
    }
  }
}
