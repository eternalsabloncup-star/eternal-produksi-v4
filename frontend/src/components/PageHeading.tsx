import type { ReactNode } from "react";

type Props = {
  /** Diabaikan — judul halaman hanya di topbar. */
  title?: string;
  /** Tidak dipakai untuk judul; diabaikan agar tidak menambah blok teks di bawah topbar. */
  subtitle?: string;
  actions?: ReactNode;
};

/**
 * Hanya merender baris aksi halaman bila ada.
 * Judul halaman tidak diulang di konten (sudah di topbar).
 */
export function PageHeading({ actions }: Props) {
  if (!actions) return null;

  return (
    <div className="ep-pageheading ep-pageheading-tools">
      <div className="ep-tools">{actions}</div>
    </div>
  );
}
