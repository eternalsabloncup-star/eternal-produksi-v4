# Eternal Production V4

Aplikasi produksi Eternal Packaging (fondasi + login minimal).

## Stack

- Frontend: React + TypeScript + Vite (npm)
- Backend: FastAPI + Pydantic v2 + PyMongo Sync
- Database: MongoDB (replica set `rs0`)
- CI: GitHub Actions (`ubuntu-latest`)

## Struktur

```text
frontend/   # UI login + /app terproteksi
backend/    # API health + auth
scripts/    # Bootstrap manual + Mongo helpers
.github/workflows/ci.yml
docker-compose.yml
```

## Menjalankan development

1. Salin env contoh:

```bash
cp .env.example .env
```

2. Jalankan stack (Mongo internal + auth, port hanya `127.0.0.1`):

```bash
chmod +x scripts/*.sh
docker compose up --build
```

- Frontend: http://127.0.0.1:5173
- Backend: http://127.0.0.1:8000/api/health
- Mongo: hanya `127.0.0.1:27017` (tidak dipublish ke `0.0.0.0`)

3. Bootstrap **manual** (sekali per environment):

```bash
# Dari mesin host dengan MONGODB_URI yang mengarah ke instance target
cd backend
pip install -r requirements.txt
cd ..
python scripts/bootstrap_mongo.py --ensure-indexes
python scripts/bootstrap_mongo.py --create-admin
```

Password admin diminta via `getpass` (bukan argumen CLI, bukan default password).

## Bootstrap policy

- Bootstrap **tidak** dijalankan saat startup API.
- Bootstrap **tidak** dijalankan saat Docker application startup.
- Bootstrap **tidak** dijalankan saat deploy.
- Index init dan admin provisioning dipisah (`--ensure-indexes` / `--create-admin`).
- Jika admin sudah ada, pembuatan admin baru ditolak.

## Auth

- Argon2id password hash
- Opaque session token (CSPRNG), disimpan sebagai SHA-256 hash
- Cookie `HttpOnly` + `SameSite=Lax` (+ `Secure` di production)
- CSRF wajib untuk mutasi terautentikasi (`X-CSRF-Token`)
- Rate limit login (in-process)
- Tidak ada registrasi publik

## Deployment policy

- Deploy aplikasi **tidak** menjalankan migration/bootstrap/reset collection.
- Perubahan dokumen diusahakan backward-compatible.
- Perubahan data yang tidak kompatibel membutuhkan evaluasi dan tindakan terpisah.
- Production deployment **belum** dibuat pada fase fondasi ini.
- Merge dan deploy tetap melalui persetujuan Owner (manual).

## Tes lokal

```bash
./scripts/start-test-mongo.sh
cd backend && pytest
cd ../frontend && npm install && npm run typecheck && npm run build
```

## Keamanan public repository

- Jangan commit `.env` atau secret nyata.
- Kredensial Compose di `.env.example` hanya untuk development lokal.
- Keyfile `scripts/mongo-dev.key` khusus development — jangan dipakai production.
