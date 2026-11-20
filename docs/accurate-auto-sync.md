# Accurate Auto-Sync (Unified) — Eternal V4

## Arsitektur

- **Worker one-shot:** `scripts/accurate_auto_sync_worker.py`
- **Bukan** loop di dalam proses FastAPI / uvicorn lifespan
- Interval target: **60 detik** via scheduler eksternal (systemd timer / cron)
- Tidak ada tombol “Sync Sekarang”

## Kill switch (default OFF)

| Flag | Default |
| --- | --- |
| `V4_PO_AUTO_SYNC_ENABLED` | `false` |
| `V4_RECEIPT_AUTO_SYNC_ENABLED` | `false` |
| `V4_SJ_SYNC_ENABLED` | `false` |
| `V4_ACCURATE_AUTO_SYNC_INTERVAL_SECONDS` | `60` |

Saat OFF: **zero** request Accurate, **zero** write Mongo dari job tersebut.

## Job

1. **po** — list/detail Sales Order berpaginasi (batas max pages/rows per siklus)
2. **receipt** — **BLOCKED** sampai kontrak endpoint Accurate terverifikasi
3. **sj** — membungkus `delivery_sync` existing; guard production tetap ketat

## Index (manual maintenance)

Collection `accurate_auto_sync_status` memerlukan index unik `uniq_auto_sync_job` pada `job`.
Jangan createIndex dari request/worker production — jalankan bootstrap maintenance Owner.

## Aktivasi production

Memerlukan keputusan Owner terpisah: enable flag, timer systemd, backup Mongo,
dan untuk SJ jalur guard production eksplisit.