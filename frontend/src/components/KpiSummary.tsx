import type { CSSProperties } from "react";
import { resolveKpiTheme } from "./kpiTheme";

export type KpiItem = {
  label: string;
  value: string | number;
  foot?: string;
  /** Legacy; warna kini dari mapping judul, tetap diterima agar pemanggil lama aman. */
  kind?: "warn" | "danger" | "";
};

type Props = {
  items: KpiItem[];
};

function themeVars(label: string): CSSProperties {
  const theme = resolveKpiTheme(label);
  return {
    ["--kpi-from" as string]: theme.from,
    ["--kpi-to" as string]: theme.to,
    ["--kpi-text" as string]: theme.text,
    ["--kpi-accent" as string]: theme.accent,
  };
}

export function KpiSummary({ items }: Props) {
  return (
    <div className="ep-kpis" role="group" aria-label="Ringkasan KPI">
      {items.map((item) => {
        const theme = resolveKpiTheme(item.label);
        const foot = item.foot || "Simulasi operasional (DEMO)";
        const valueText = String(item.value);
        return (
          <div
            key={item.label}
            className="ep-kpi"
            data-kpi={theme.key}
            style={themeVars(item.label)}
            title={`${item.label}: ${valueText} — ${foot}`}
            aria-label={`${item.label}: ${valueText}. ${foot}`}
          >
            <div className="klabel">{item.label}</div>
            <div className="kval">{item.value}</div>
            <span className="ep-sr-only">{foot}</span>
          </div>
        );
      })}
    </div>
  );
}
