/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Inbox source: `api`|`accurate` = Mongo V4; `demo` = fixture.
   * Unset: development → demo; production build → api (lihat .env.production).
   */
  readonly VITE_INBOX_PO_SOURCE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Resolusi Vite ke build ESM; tipe mengikuti paket pdfjs-dist. */
declare module "pdfjs-dist/build/pdf.mjs" {
  export * from "pdfjs-dist";
}
