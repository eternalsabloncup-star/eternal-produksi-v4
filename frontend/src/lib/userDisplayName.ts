/**
 * Presentasi identitas pengguna operasional — Master display_name.
 * Email tetap identitas teknis/storage; tidak ditampilkan sebagai label aktor.
 */

export const USER_NAME_UNAVAILABLE = "Nama belum tersedia";

/** Label tampilan dari display_name Master; tanpa fallback email. */
export function userDisplayName(
  displayName: string | null | undefined,
): string {
  const name = String(displayName || "").trim();
  return name || USER_NAME_UNAVAILABLE;
}

/**
 * Label aktor dari payload yang mungkin punya actor_display_name (enrichment)
 * atau hanya display_name / name.
 */
export function actorDisplayLabel(opts: {
  actor_display_name?: string | null;
  display_name?: string | null;
  name?: string | null;
}): string {
  return userDisplayName(
    opts.actor_display_name || opts.display_name || opts.name,
  );
}
