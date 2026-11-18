"""Payload contoh struktur Accurate untuk uji normalisasi.

Identitas ID mengikuti contoh Owner. Data pelanggan/barang disintesis —
bukan data pelanggan nyata.
"""

from __future__ import annotations

from typing import Any

# Contoh Owner: PO/18/11/2026/000010, accurate_id 95106, lines 106318 & 106319.
SAMPLE_SALES_ORDER_STRUCTURE: dict[str, Any] = {
    "id": 95106,
    "number": "PO/18/11/2026/000010",
    "transDate": "18/11/2026",
    "customer": {"name": "Pelanggan Simulasi V4 (BUKAN DATA NYATA)"},
    "description": "Fixture struktur Tahap 01",
    "detailItem": [
        {
            "id": 106318,
            "quantity": 1000,
            "item": {
                "no": "BRG-SIM-001",
                "name": "Barang Simulasi Cetak",
            },
            "itemUnit": {"name": "PCS"},
            "charField1": "Cetak",
            "charField2": "Hijau Tua",
        },
        {
            "id": 106319,
            "quantity": 50,
            "item": {
                "no": "BRG-SIM-002",
                "name": "Barang Simulasi Tidak Cetak",
            },
            "itemUnit": {"name": "PACK"},
            "charField1": "Tidak Cetak",
            "charField2": "Putih",
        },
    ],
}
