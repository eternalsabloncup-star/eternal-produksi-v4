/**
 * Item & Progres vertikal (label : nilai) untuk popup Tugas Tim.
 * Bukan tabel horizontal. Multi-item = grup terpisah + divider tipis.
 */

import type { ReactNode } from "react";
import { WorkSheetKvGroup, WorkSheetStackRow } from "./compactTaskSheet";
import {
  ITEM_NAME_FALLBACK,
  type TaskItemLineView,
} from "./teamTaskUi";

function formatQty(n: number | undefined): string {
  if (n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("id-ID");
}

export type TaskItemProgressField = {
  label: string;
  value: ReactNode;
  testId?: string;
  numeric?: boolean;
  title?: string;
};

type Props = {
  items: TaskItemLineView[];
  /** Field level-tugas (Qty Alokasi/Target, Warna Cat, Status, …). */
  fields?: TaskItemProgressField[];
  /** Tampilkan Qty per baris produk bila qty tersedia. */
  showItemQty?: boolean;
  testId?: string;
};

export function TaskItemProgress({
  items,
  fields = [],
  showItemQty = true,
  testId = "task-progress-kv",
}: Props) {
  const rows =
    items.length > 0
      ? items
      : [
          {
            accurateLineId: "",
            itemName: ITEM_NAME_FALLBACK,
            qty: null,
            unit: "PCS",
          } satisfies TaskItemLineView,
        ];

  return (
    <div className="ep-task-progress-block" data-testid="task-progress-block">
      <p className="ep-sectionlabel">Item & Progres</p>
      <div data-testid={testId}>
        {rows.map((line, idx) => (
          <div key={line.accurateLineId || `${line.itemName}-${idx}`}>
            {idx > 0 ? <hr className="ep-item-progress-divider" /> : null}
            <WorkSheetKvGroup>
              <WorkSheetStackRow
                label="Nama Barang"
                value={
                  <span
                    data-testid={
                      idx === 0 ? "task-popup-item-name" : undefined
                    }
                  >
                    {line.itemName || ITEM_NAME_FALLBACK}
                  </span>
                }
              />
              {showItemQty && line.qty != null ? (
                <WorkSheetStackRow
                  label="Qty"
                  value={
                    <span className="ep-num">
                      {formatQty(line.qty)} {line.unit.toLowerCase()}
                    </span>
                  }
                />
              ) : null}
            </WorkSheetKvGroup>
          </div>
        ))}
        {fields.length > 0 ? (
          <WorkSheetKvGroup>
            {fields.map((f) => (
              <WorkSheetStackRow
                key={f.label}
                label={f.label}
                value={
                  f.testId ? (
                    <span
                      className={f.numeric ? "ep-num" : undefined}
                      data-testid={f.testId}
                      title={f.title}
                    >
                      {f.value}
                    </span>
                  ) : f.numeric ? (
                    <span className="ep-num" title={f.title}>
                      {f.value}
                    </span>
                  ) : (
                    f.value
                  )
                }
              />
            ))}
          </WorkSheetKvGroup>
        ) : null}
      </div>
    </div>
  );
}
