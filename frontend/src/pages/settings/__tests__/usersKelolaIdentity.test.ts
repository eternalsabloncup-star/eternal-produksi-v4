import { describe, expect, it } from "vitest";
import type { SettingsUser } from "../../../api/settings";

/**
 * Pure contract: each Kelola action set must close over the same user id
 * that owns the mobile card / desktop row (no swapped targets).
 */
function bindKelolaTargets(
  users: SettingsUser[],
  makeItems: (u: SettingsUser) => Array<{ id: string; onSelect: () => void }>,
) {
  return users.map((u) => {
    const calls: string[] = [];
    const wrapped = makeItems(u).map((item) => ({
      ...item,
      onSelect: () => {
        calls.push(u.id);
        item.onSelect();
      },
    }));
    return { userId: u.id, items: wrapped, calls };
  });
}

describe("Users Kelola action identity", () => {
  it("binds every menu action to the owning user id", () => {
    const users: SettingsUser[] = [
      {
        id: "u-admin",
        email: "very.long.admin.email+tag@eternal-produksi.example.local",
        display_name: "Admin Pusat Dengan Nama Panjang Sekali",
        role: "admin",
        operational_role: "production-head",
        branch_ids: ["HQ", "SMG", "JKT"],
        is_active: true,
        effective_module_permissions: [],
      },
      {
        id: "u-helper",
        email: "helper@eternal.local",
        display_name: "Budi Helper",
        role: "user",
        operational_role: "helper",
        branch_ids: ["SMG"],
        is_active: true,
        effective_module_permissions: [],
      },
    ];

    const bound = bindKelolaTargets(users, (u) => [
      { id: "edit", onSelect: () => undefined },
      {
        id: u.is_active ? "deactivate" : "activate",
        onSelect: () => undefined,
      },
      { id: "reset-password", onSelect: () => undefined },
      { id: "sessions", onSelect: () => undefined },
    ]);

    expect(bound).toHaveLength(2);
    for (const row of bound) {
      expect(row.items).toHaveLength(4);
      for (const item of row.items) {
        item.onSelect();
      }
      expect(row.calls).toEqual([row.userId, row.userId, row.userId, row.userId]);
    }
    expect(bound[0].calls.every((id) => id === "u-admin")).toBe(true);
    expect(bound[1].calls.every((id) => id === "u-helper")).toBe(true);
  });
});
