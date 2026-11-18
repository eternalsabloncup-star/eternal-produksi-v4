export type KpiItem = {
  label: string;
  value: string | number;
  foot?: string;
  kind?: "warn" | "danger" | "";
};

type Props = {
  items: KpiItem[];
};

export function KpiSummary({ items }: Props) {
  return (
    <div className="ep-kpis" role="group" aria-label="Ringkasan KPI">
      {items.map((item) => (
        <div key={item.label} className={`ep-kpi ${item.kind || ""}`}>
          <div className="klabel">{item.label}</div>
          <div className="kval">{item.value}</div>
          <div className="kfoot">{item.foot || "Simulasi operasional (DEMO)"}</div>
        </div>
      ))}
    </div>
  );
}
