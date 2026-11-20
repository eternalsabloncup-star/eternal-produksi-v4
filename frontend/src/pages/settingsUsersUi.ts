/** Helpers UI Pengaturan → Pengguna (cabang) — pure, mudah diuji. */

export type BranchOption = {
  code: string;
  name: string;
  is_active: boolean;
};

export function formatUserBranches(
  branchIds: string[] | null | undefined,
  catalog?: Array<Pick<BranchOption, "code" | "name">>,
): string {
  const ids = (branchIds || []).map((x) => String(x || "").trim()).filter(Boolean);
  if (ids.length === 0) return "Belum ditentukan";
  const nameByCode = new Map(
    (catalog || []).map((b) => [b.code.toUpperCase(), b.name] as const),
  );
  return ids
    .map((code) => {
      const key = code.toUpperCase();
      const name = nameByCode.get(key);
      return name ? `${key} — ${name}` : key;
    })
    .join(", ");
}

export function activeBranchOptions(branches: BranchOption[]): BranchOption[] {
  return branches.filter((b) => b.is_active);
}

export function toggleBranchId(selected: string[], code: string, checked: boolean): string[] {
  const key = code.trim().toUpperCase();
  if (!key) return selected;
  const set = new Set(selected.map((x) => x.toUpperCase()));
  if (checked) set.add(key);
  else set.delete(key);
  return Array.from(set);
}

export function sameBranchSet(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  const left = new Set((a || []).map((x) => x.toUpperCase()));
  const right = new Set((b || []).map((x) => x.toUpperCase()));
  if (left.size !== right.size) return false;
  for (const x of left) if (!right.has(x)) return false;
  return true;
}
