/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `api` = Inbox Accurate dari Mongo V4; default / kosong = DEMO. */
  readonly VITE_INBOX_PO_SOURCE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Resolusi Vite ke build ESM; tipe mengikuti paket pdfjs-dist. */
declare module "pdfjs-dist/build/pdf.mjs" {
  export * from "pdfjs-dist";
}
