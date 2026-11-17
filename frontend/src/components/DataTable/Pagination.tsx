type Props = {
  page: number;
  pageSize: number;
  total: number;
  pageSizeOptions?: number[];
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
};

export function Pagination({
  page,
  pageSize,
  total,
  pageSizeOptions = [15, 25, 50],
  onPageChange,
  onPageSizeChange,
}: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pages);
  const from = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const to = Math.min(safePage * pageSize, total);

  return (
    <div className="ep-tablefoot">
      <span>
        Menampilkan {from}–{to} dari <strong>{total}</strong> baris
      </span>
      <div className="pages">
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          aria-label="Ukuran halaman"
          style={{
            border: "1px solid #dbe4ee",
            borderRadius: 5,
            color: "#61728c",
            fontSize: 10,
            height: 28,
          }}
        >
          {pageSizeOptions.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ep-btn sm"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
        >
          ←
        </button>
        <span style={{ padding: 6 }}>
          {safePage} / {pages}
        </span>
        <button
          type="button"
          className="ep-btn sm"
          disabled={safePage >= pages}
          onClick={() => onPageChange(safePage + 1)}
        >
          →
        </button>
      </div>
    </div>
  );
}
