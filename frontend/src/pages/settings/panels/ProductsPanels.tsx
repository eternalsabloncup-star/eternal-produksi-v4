import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  fetchSettingsProducts,
  saveProductScreenSize,
} from "../../../api/settings";

type ProductItem = { item_code: string; screen_size?: string };

type ListProps = {
  onError: (msg: string | null) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onFeedback?: (tone: "success" | "error", title: string, message: string) => void;
  loadFailed?: boolean;
  onRetry?: () => void;
};

export function ProductsListPanel({
  onError,
  onDirtyChange,
  onFeedback,
  loadFailed,
  onRetry,
}: ListProps) {
  const [items, setItems] = useState<ProductItem[]>([]);
  const [unsetLabel, setUnsetLabel] = useState("Belum diatur");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [itemCode, setItemCode] = useState("");
  const [screenSize, setScreenSize] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = async () => {
    setLoading(true);
    setFailed(false);
    try {
      const res = await fetchSettingsProducts();
      setItems(res.items);
      setUnsetLabel(res.screen_unset_label);
    } catch (err) {
      setFailed(true);
      // Keep previously loaded items — LOAD_FAILED ≠ DATA_EMPTY.
      onError(err instanceof Error ? err.message : "Gagal memuat daftar produk.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount load
  }, []);

  useEffect(() => {
    onDirtyChange?.(Boolean(itemCode.trim()) || Boolean(screenSize.trim()));
  }, [itemCode, screenSize, onDirtyChange]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(
      (it) =>
        it.item_code.toLowerCase().includes(needle) ||
        (it.screen_size || "").toLowerCase().includes(needle),
    );
  }, [items, q]);

  async function onSaveScreen(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    onError(null);
    try {
      await saveProductScreenSize(itemCode.trim(), screenSize);
      const res = await fetchSettingsProducts();
      setItems(res.items);
      setItemCode("");
      setScreenSize("");
      onFeedback?.("success", "Berhasil", "Ukuran screen berhasil disimpan.");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Gagal menyimpan ukuran screen.";
      onError(msg);
      onFeedback?.("error", "Gagal", msg);
    } finally {
      setBusy(false);
    }
  }

  const showFail = failed || loadFailed;

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Master produk</h3>
        <p className="ep-settings-card-note">
          Satu sumber data produk. Atur ukuran screen di form detail — tidak ada master
          produk kedua.
        </p>
        {showFail ? (
          <div className="ep-settings-load-banner is-error" role="alert">
            <p>Gagal memuat produk. Ini bukan berarti data kosong.</p>
            <button
              type="button"
              className="ep-btn"
              onClick={() => {
                onRetry?.();
                void reload();
              }}
            >
              Muat ulang
            </button>
          </div>
        ) : null}
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Kode barang atau ukuran…"
              aria-label="Cari produk"
            />
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th>Kode barang</th>
                <th style={{ width: 180 }}>Ukuran screen</th>
                <th className="is-actions">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={3}>
                    <div className="ep-settings-empty">Memuat produk…</div>
                  </td>
                </tr>
              ) : showFail ? (
                <tr>
                  <td colSpan={3}>
                    <div className="ep-settings-empty">
                      Muat ulang untuk menampilkan data.
                    </div>
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={3}>
                    <div className="ep-settings-empty">
                      {items.length === 0
                        ? "Belum ada data produk."
                        : "Tidak ada produk yang cocok."}
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((it) => (
                  <tr key={it.item_code}>
                    <td>{it.item_code}</td>
                    <td>{it.screen_size?.trim() ? it.screen_size : unsetLabel}</td>
                    <td className="is-actions">
                      <button
                        type="button"
                        className="ep-btn"
                        onClick={() => {
                          setItemCode(it.item_code);
                          setScreenSize(it.screen_size || "");
                        }}
                      >
                        Ubah ukuran
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Detail ukuran screen</h3>
        <form className="ep-settings-form-grid" onSubmit={onSaveScreen}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode barang</span>
            <input
              value={itemCode}
              onChange={(e) => setItemCode(e.target.value)}
              required
              list="settings-product-codes-hub"
            />
            <datalist id="settings-product-codes-hub">
              {items.map((it) => (
                <option key={it.item_code} value={it.item_code} />
              ))}
            </datalist>
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Ukuran screen</span>
            <input
              value={screenSize}
              onChange={(e) => setScreenSize(e.target.value)}
              placeholder={unsetLabel}
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${busy ? " is-busy" : ""}`}
              disabled={busy || !itemCode.trim()}
            >
              {busy ? "Menyimpan…" : "Simpan ukuran screen"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}

type ScreenProps = {
  onError: (msg: string | null) => void;
  onDirtyChange: (dirty: boolean) => void;
  onFeedback: (tone: "success" | "error", title: string, message: string) => void;
};

export function ProductScreenPanel({
  onError,
  onDirtyChange,
  onFeedback,
}: ScreenProps) {
  const [items, setItems] = useState<ProductItem[]>([]);
  const [unsetLabel, setUnsetLabel] = useState("Belum diatur");
  const [itemCode, setItemCode] = useState("");
  const [screenSize, setScreenSize] = useState("");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");

  useEffect(() => {
    onDirtyChange(Boolean(itemCode.trim()) || Boolean(screenSize.trim()));
  }, [itemCode, screenSize, onDirtyChange]);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetchSettingsProducts();
        setItems(res.items);
        setUnsetLabel(res.screen_unset_label);
      } catch (err) {
        onError(err instanceof Error ? err.message : "Gagal memuat Master Produk.");
      }
    })();
  }, [onError]);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    onError(null);
    try {
      await saveProductScreenSize(itemCode.trim(), screenSize);
      const res = await fetchSettingsProducts();
      setItems(res.items);
      setItemCode("");
      setScreenSize("");
      onFeedback("success", "Berhasil", "Ukuran screen berhasil disimpan.");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Gagal menyimpan ukuran screen.";
      onError(msg);
      onFeedback("error", "Gagal", msg);
    } finally {
      setBusy(false);
    }
  }

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((it) => it.item_code.toLowerCase().includes(needle));
  }, [items, q]);

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Simpan ukuran screen</h3>
        <p className="ep-settings-card-note">
          Mengubah data produk yang sama dengan Master Produk. Kosong = {unsetLabel}.
        </p>
        <form className="ep-settings-form-grid" onSubmit={onSave}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode barang</span>
            <input
              value={itemCode}
              onChange={(e) => setItemCode(e.target.value)}
              required
              list="settings-product-codes"
            />
            <datalist id="settings-product-codes">
              {items.map((it) => (
                <option key={it.item_code} value={it.item_code} />
              ))}
            </datalist>
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Ukuran screen</span>
            <input
              value={screenSize}
              onChange={(e) => setScreenSize(e.target.value)}
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${busy ? " is-busy" : ""}`}
              disabled={busy || !itemCode.trim()}
            >
              {busy ? "Menyimpan…" : "Simpan ukuran screen"}
            </button>
          </div>
        </form>
      </section>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Ringkas ukuran screen</h3>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari kode barang</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th>Kode barang</th>
                <th style={{ width: 180 }}>Ukuran screen</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={2}>
                    <div className="ep-settings-empty">Belum ada data.</div>
                  </td>
                </tr>
              ) : (
                filtered.map((it) => (
                  <tr key={it.item_code}>
                    <td>{it.item_code}</td>
                    <td>{it.screen_size?.trim() ? it.screen_size : unsetLabel}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
