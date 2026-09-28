/// <reference types="vite/client" />

/** De versie die bij het bouwen in de bundel is gebakken (frontend/Dockerfile).
 *  Leeg tijdens `npm run dev` — dan toont het scherm "dev". */
interface ImportMetaEnv {
  readonly VITE_LABX_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
