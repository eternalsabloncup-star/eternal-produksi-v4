export type SettingsUxMode = "simple" | "advanced";

const STORAGE_KEY = "ep-settings-ux-mode-v1";

export function readSettingsUxMode(): SettingsUxMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "advanced" || v === "simple") return v;
  } catch {
    // ignore
  }
  return "simple";
}

export function writeSettingsUxMode(mode: SettingsUxMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // ignore
  }
}

export function settingsUxModeLabel(mode: SettingsUxMode): string {
  return mode === "simple" ? "Mode sederhana" : "Mode lanjutan";
}
