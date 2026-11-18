import type { DoneFilter } from "../demo/teamTaskWork";

type Props = {
  value: DoneFilter;
  doneCount: number;
  openCount: number;
  onChange: (next: DoneFilter) => void;
};

/** Toolbar Semua / Sudah / Belum dengan badge angka dari sumber data role. */
export function TeamTaskDoneFilter({
  value,
  doneCount,
  openCount,
  onChange,
}: Props) {
  return (
    <div className="ep-task-donebar" role="toolbar" aria-label="Filter penyelesaian">
      <button
        type="button"
        className={`ep-task-donechip${value === "all" ? " is-active" : ""}`}
        aria-pressed={value === "all"}
        onClick={() => onChange("all")}
      >
        Semua
      </button>
      <button
        type="button"
        className={`ep-task-donechip${value === "done" ? " is-active" : ""}`}
        aria-pressed={value === "done"}
        onClick={() => onChange("done")}
      >
        Sudah Dikerjakan
        <span className="ep-task-donebadge is-done" aria-label={`${doneCount} sudah`}>
          {doneCount}
        </span>
      </button>
      <button
        type="button"
        className={`ep-task-donechip${value === "open" ? " is-active" : ""}`}
        aria-pressed={value === "open"}
        onClick={() => onChange("open")}
      >
        Belum Dikerjakan
        <span className="ep-task-donebadge is-open" aria-label={`${openCount} belum`}>
          {openCount}
        </span>
      </button>
    </div>
  );
}
