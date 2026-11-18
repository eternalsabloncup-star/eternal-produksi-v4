type Props = {
  children?: React.ReactNode;
};

export function DemoBanner({ children }: Props) {
  return (
    <div className="ep-demo-banner" role="status">
      {children ??
        "● DEMO — Data simulasi lokal. Bukan data operasional / pelanggan nyata. Tidak menulis MongoDB."}
    </div>
  );
}
