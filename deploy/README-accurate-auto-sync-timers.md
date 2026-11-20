# Timer Auto-Sync Accurate (PO / Invoice / Receipt)

Unit production dipasang di VPS (`/etc/systemd/system/`). Salinan di folder
ini adalah dokumentasi/template — **bukan** auto-deploy.

## Jadwal

| Job | OnCalendar | Offset vs PO |
| --- | --- | --- |
| PO | `*:0/5:00` | 0s |
| Invoice | `*:1/5:30` | +90s |
| Receipt | `*:3/5:00` | +180s |

Wrapper `scripts/vps_accurate_job_flock.sh` memakai `flock` global agar tiga
job tidak bentrok request Accurate. Lock Mongo per-job tetap aktif di worker.

## Kill switch

Flags di `/etc/eternal-v4/backend/backend.env`:

- `V4_PO_AUTO_SYNC_ENABLED`
- `V4_INVOICE_AUTO_SYNC_ENABLED`
- `V4_RECEIPT_AUTO_SYNC_ENABLED`
- `V4_SJ_SYNC_ENABLED` (tetap 0 sampai CEO)
- `V4_SJ_SYNC_PRODUCTION_ENABLED` (tetap 0 sampai CEO + PR guard)

Matikan flag terkait untuk menghentikan write tanpa men-disable timer.

## SJ

Jangan pasang timer SJ sampai `V4_SJ_SYNC_PRODUCTION_ENABLED` disetujui CEO
dan production guard lulus merge/deploy.
