import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle: string;
  actions?: ReactNode;
};

export function PageHeading({ title, subtitle, actions }: Props) {
  return (
    <div className="ep-pageheading">
      <div>
        <div className="ep-eyebrow">Eternal Production / V4</div>
        <h2>{title}</h2>
        <div className="ep-sub">{subtitle}</div>
      </div>
      {actions ? <div className="ep-tools">{actions}</div> : null}
    </div>
  );
}
