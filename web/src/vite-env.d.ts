/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of a live FastAPI instance. Unset means read the static
   *  snapshot exported from Postgres (see src/staticGraph.ts). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
