/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Inbox source: `api`|`accurate` = Mongo V4; `demo` = fixture.
   * Unset: development → demo; production build → api (lihat .env.production).
   * Cursor preview: `.env.development` sets `api` so UI memakai panel production.
   */
  readonly VITE_INBOX_PO_SOURCE?: string;
  /**
   * Dev-only: intercept `/api/*` with local fixtures (MSW-style fetch mock).
   * Must never be enabled in production build.
   */
  readonly VITE_API_SIMULATION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Resolusi Vite ke build ESM; tipe mengikuti paket pdfjs-dist. */
declare module "pdfjs-dist/build/pdf.mjs" {
  export * from "pdfjs-dist";
}
