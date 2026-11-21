import { useMemo } from "react";
import type { SnakeStageView, SnakeVisualState } from "./snakeMapping";

type Props = {
  stages: SnakeStageView[];
};

const COLS = 5;
const VB_W = 380;
const VB_H = 252;
const PAD_X = 44;
const PAD_Y = 18;
const ROW_GAP = 78;
const COL_GAP = (VB_W - PAD_X * 2) / (COLS - 1);
/** Radius belokan U = setengah jarak antar-baris (setengah lingkaran). */
const TURN_RADIUS = ROW_GAP / 2;

function nodeCenter(index: number): { x: number; y: number } {
  const row = Math.floor(index / COLS);
  const colInRow = index % COLS;
  const col = row % 2 === 0 ? colInRow : COLS - 1 - colInRow;
  return {
    x: PAD_X + col * COL_GAP,
    y: PAD_Y + row * ROW_GAP,
  };
}

function ringFor(visual: SnakeVisualState): string {
  if (visual === "done") return "#1a4d3e";
  if (visual === "active") return "#e8872a";
  if (visual === "blocked") return "#b44152";
  if (visual === "na") return "#b8c0bc";
  return "#c5ccc8";
}

function fillFor(visual: SnakeVisualState): string {
  if (visual === "done") return "#1a4d3e";
  if (visual === "na") return "#eef1ef";
  return "#ffffff";
}

function wrapLabel(label: string): string[] {
  const words = label.split(" ");
  if (words.length <= 1) return [label];
  if (label.length <= 10) return [label];
  if (words.length === 2) return words;
  return [
    words.slice(0, Math.ceil(words.length / 2)).join(" "),
    words.slice(Math.ceil(words.length / 2)).join(" "),
  ];
}

/**
 * Snake 15 tahap — path SVG terukur dengan belokan U melengkung.
 */
export function SnakeProgress({ stages }: Props) {
  const points = useMemo(() => stages.map((_, i) => nodeCenter(i)), [stages]);

  const lit = (v: SnakeVisualState) => v === "done" || v === "active";

  const basePath = useMemo(() => {
    if (points.length < 2) return "";
    let d = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      const b = points[i];
      const rowA = Math.floor((i - 1) / COLS);
      const rowB = Math.floor(i / COLS);
      if (rowA === rowB) {
        d += ` L ${b.x} ${b.y}`;
      } else {
        // Belokan U: setengah lingkaran — kanan (row 0→1), kiri (row 1→2).
        const sweep = rowA % 2 === 0 ? 1 : 0;
        d += ` A ${TURN_RADIUS} ${TURN_RADIUS} 0 0 ${sweep} ${b.x} ${b.y}`;
      }
    }
    return d;
  }, [points]);

  return (
    <div className="elt-snake" data-snake="1">
      <svg
        className="elt-snake-svg"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        role="img"
        aria-label="Progress pesanan 15 tahap"
      >
        <path
          d={basePath}
          fill="none"
          stroke="#d5dad6"
          strokeWidth={3}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {stages.slice(1).map((_, idx) => {
          const i = idx + 1;
          if (!lit(stages[i - 1].visual) || !lit(stages[i].visual)) return null;
          const a = points[i - 1];
          const b = points[i];
          const rowA = Math.floor((i - 1) / COLS);
          const rowB = Math.floor(i / COLS);
          const stroke = "#e8872a";
          if (rowA === rowB) {
            return (
              <line
                key={`seg-${i}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={stroke}
                strokeWidth={3.5}
                strokeLinecap="round"
              />
            );
          }
          const sweep = rowA % 2 === 0 ? 1 : 0;
          return (
            <path
              key={`seg-${i}`}
              d={`M ${a.x} ${a.y} A ${TURN_RADIUS} ${TURN_RADIUS} 0 0 ${sweep} ${b.x} ${b.y}`}
              fill="none"
              stroke={stroke}
              strokeWidth={3.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          );
        })}

        {stages.map((stage, i) => {
          const { x, y } = points[i];
          const v = stage.visual;
          const lines = wrapLabel(stage.label);
          return (
            <g key={stage.seq}>
              <circle
                cx={x}
                cy={y}
                r={11}
                fill={fillFor(v)}
                stroke={ringFor(v)}
                strokeWidth={v === "active" || v === "blocked" ? 2.6 : 1.7}
              />
              {v === "done" ? (
                <path
                  d={`M ${x - 4.5} ${y + 0.4} L ${x - 1.4} ${y + 3.4} L ${x + 5} ${y - 3.4}`}
                  fill="none"
                  stroke="#fff"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
              {v === "blocked" ? (
                <text
                  x={x}
                  y={y + 0.5}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fill="#b44152"
                  fontSize={12}
                  fontWeight={700}
                  fontFamily="DM Sans, system-ui, sans-serif"
                >
                  !
                </text>
              ) : null}
              <text
                x={x}
                y={y + 22}
                textAnchor="middle"
                fill={v === "na" ? "#8a9690" : "#1a2a22"}
                fontSize={9}
                fontWeight={600}
                fontFamily="DM Sans, system-ui, sans-serif"
              >
                {lines.map((line, li) => (
                  <tspan key={li} x={x} dy={li === 0 ? 0 : 10}>
                    {line}
                  </tspan>
                ))}
              </text>
              <title>
                {stage.label}: {stage.detail}
              </title>
            </g>
          );
        })}
      </svg>
      <ol className="elt-snake-sr">
        {stages.map((s) => (
          <li key={s.seq}>
            {s.seq}. {s.label} — {s.visual}
          </li>
        ))}
      </ol>
    </div>
  );
}
