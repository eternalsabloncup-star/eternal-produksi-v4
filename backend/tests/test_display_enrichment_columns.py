"""Regresi kolom tampilan Jadwal / Tugas KP: outlet, warna cat, ship_dates."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import display_enrichment as enrich
from app.services import scheduling_service
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NC = "106319"


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _sj(
    *,
    sj_id: str,
    ship_date: str,
    line_id: str = LINE_PRINT,
    qty: float = 10,
    superseded_by: str | None = None,
) -> dict:
    payload = {
        "id": sj_id,
        "number": f"SJ/{sj_id}",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": ship_date,
        "shipDate": ship_date,
        "toAddress": "ALAMAT",
        "shipmentName": "OUTLET",  # bukan nama customer
        "customerName": "PELANGGAN-ASLI",
        "detailItem": [
            {
                "id": f"SJL-{sj_id}",
                "salesOrderDetailId": line_id,
                "itemNo": "BRG",
                "quantity": qty,
                "itemUnitName": "PCS",
            }
        ],
    }
    if superseded_by:
        payload["superseded_by"] = superseded_by
    return payload


def test_outlet_is_customer_not_expedition(db: Database) -> None:
    po = _seed_po(db)
    # Sample PO customer ≠ expedition OUTLET
    name = enrich.outlet_name_from_po(po)
    assert name
    assert name.upper() != "OUTLET"
    header = po.get("header") or {}
    assert name == str(header.get("customer") or "")


def test_paint_color_from_item_not_print_color(db: Database) -> None:
    po = _seed_po(db)
    line = next(
        i
        for i in (po["header"]["items"])
        if str(i.get("accurate_line_id")) == LINE_PRINT
    )
    color = enrich.paint_color_from_line(line)
    assert color  # sample has color
    # print_color alokasi tidak boleh dipakai — helper hanya baca line.color
    fake = {**line, "color": "PUTIH"}
    assert enrich.paint_color_from_line(fake) == "PUTIH"


def test_ship_dates_empty_without_sj(db: Database) -> None:
    _seed_po(db)
    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    assert notes == []
    assert enrich.ship_dates_by_line_id(notes) == {}


def test_single_and_multi_sj_dates(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-A", ship_date="25/11/2026"))
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-B", ship_date="27/11/2026"))
    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    by_line = enrich.ship_dates_by_line_id(notes)
    assert by_line[LINE_PRINT] == ["25/11/2026", "27/11/2026"]


def test_duplicate_dates_dedupe(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-A", ship_date="25/11/2026"))
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-B", ship_date="25/11/2026"))
    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    assert enrich.ship_dates_by_line_id(notes)[LINE_PRINT] == ["25/11/2026"]


def test_partial_sj_only_matching_line(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(
        db, _sj(sj_id="SJ-NC", ship_date="26/11/2026", line_id=LINE_NC)
    )
    by_line = enrich.ship_dates_by_line_id(
        enrich.list_active_delivery_notes_for_po(db, PO_ID)
    )
    assert LINE_PRINT not in by_line or by_line.get(LINE_PRINT) == []
    assert by_line.get(LINE_NC) == ["26/11/2026"]


def test_superseded_sj_excluded(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-OLD", ship_date="20/11/2026"))
    deliveries.mark_superseded(db, accurate_sj_id="SJ-OLD", replacement_sj_id="SJ-NEW")
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-NEW", ship_date="28/11/2026"))
    by_line = enrich.ship_dates_by_line_id(
        enrich.list_active_delivery_notes_for_po(db, PO_ID)
    )
    assert by_line[LINE_PRINT] == ["28/11/2026"]


def test_line_schedule_enrichment_fields(db: Database) -> None:
    po = _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    by_line = enrich.ship_dates_by_line_id(notes)
    state = scheduling_service.line_schedule_state(
        db,
        po,
        LINE_PRINT,
        outlet_name=enrich.outlet_name_from_po(po),
        ship_dates=by_line.get(LINE_PRINT, []),
    )
    assert state["outlet_name"] == enrich.outlet_name_from_po(po)
    assert state["paint_color"] == enrich.paint_color_from_line(
        next(i for i in po["header"]["items"] if i["accurate_line_id"] == LINE_PRINT)
    )
    assert state["ship_dates"] == ["25/11/2026"]
    # print_color pada alokasi tidak diubah / tidak jadi paint_color
    assert state["paint_color"] != "Hitam-Fake"


def test_task_enrichment_po_line_and_sj(db: Database) -> None:
    po = _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    tasks = [
        {
            "task_key": "t-po-line",
            "task_type": "penjadwalan",
            "ref": {
                "accurate_po_id": PO_ID,
                "accurate_line_id": LINE_PRINT,
            },
        },
        {
            "task_key": "t-sj",
            "task_type": "qty-loading-sj-berbeda",
            "ref": {
                "accurate_po_id": PO_ID,
                "accurate_delivery_note_id": "SJ-1",
            },
        },
        {
            "task_key": "t-orphan",
            "task_type": "payment-bypass",
            "ref": {},
        },
    ]
    enrich.enrich_operational_tasks(db, tasks)
    assert tasks[0]["outlet_name"] == enrich.outlet_name_from_po(po)
    assert tasks[0]["ship_dates"] == ["25/11/2026"]
    assert tasks[1]["ship_dates"] == ["25/11/2026"]
    assert tasks[1]["outlet_name"] == enrich.outlet_name_from_po(po)
    assert tasks[2]["outlet_name"] == ""
    assert tasks[2]["ship_dates"] == []


def test_enrich_actor_display_names_batch(db: Database) -> None:
    from tests.scheduling_operator_fixtures import upsert_test_user

    upsert_test_user(
        db,
        email="screener@eternal.local",
        display_name="Screener Satu",
        operational_role="screener",
    )
    upsert_test_user(
        db,
        email="kp@eternal.local",
        display_name="Kepala Produksi",
        operational_role="production-head",
    )
    rows = [
        {"actor_email": "screener@eternal.local", "qty": 10},
        {"actor_email": "unknown@eternal.local", "qty": 1},
    ]
    enrich.enrich_actor_display_names(db, rows)
    assert rows[0]["actor_display_name"] == "Screener Satu"
    assert rows[1]["actor_display_name"] == "Nama belum tersedia"
    # Storage email identity preserved.
    assert rows[0]["actor_email"] == "screener@eternal.local"

    cancelled = [
        {
            "actor_email": "screener@eternal.local",
            "cancelled_by": "kp@eternal.local",
        },
        {
            "actor_email": "screener@eternal.local",
            "cancelled_by": "ghost@eternal.local",
        },
        {"actor_email": "screener@eternal.local"},
    ]
    enrich.enrich_actor_display_names(db, cancelled)
    enrich.enrich_actor_display_names(
        db,
        cancelled,
        email_field="cancelled_by",
        display_field="cancelled_by_display_name",
    )
    assert cancelled[0]["cancelled_by_display_name"] == "Kepala Produksi"
    assert cancelled[0]["cancelled_by"] == "kp@eternal.local"
    assert cancelled[1]["cancelled_by_display_name"] == "Nama belum tersedia"
    assert "cancelled_by_display_name" not in cancelled[2]


def test_task_enrichment_item_name_from_po_line(db: Database) -> None:
    """Nama Barang dari PO line via accurate_line_id — bukan item_code sebagai nama."""
    po = _seed_po(db)
    line = next(
        i
        for i in (po["header"]["items"])
        if str(i.get("accurate_line_id")) == LINE_PRINT
    )
    expected_name = str(line.get("item_name") or "").strip()
    expected_code = str(line.get("item_code") or "").strip()
    assert expected_name  # sample SoT harus punya nama
    before_doc = db["operational_tasks"].find_one({})  # may be None
    tasks = [
        {
            "task_key": "t-screen",
            "task_type": "screen",
            "ref": {
                "accurate_po_id": PO_ID,
                "accurate_line_id": LINE_PRINT,
                "item_code": expected_code,
                "allocation_id": f"alloc:{LINE_PRINT}:1",
            },
            "context": {"qty_alokasi": 100},
        },
        {
            "task_key": "t-other-line",
            "task_type": "persiapan-cup",
            "ref": {
                "accurate_po_id": PO_ID,
                "accurate_line_id": LINE_NC,
            },
        },
        {
            "task_key": "t-missing-line",
            "task_type": "cetak",
            "ref": {
                "accurate_po_id": PO_ID,
                "accurate_line_id": "LINE-DOES-NOT-EXIST",
            },
        },
    ]
    enrich.enrich_operational_tasks(db, tasks)
    assert tasks[0]["item_name"] == expected_name
    assert tasks[0]["ref"]["item_name"] == expected_name
    assert tasks[0]["ref"]["item_code"] == expected_code
    # Line berbeda → nama berbeda (bila ada)
    other = next(
        (
            i
            for i in po["header"]["items"]
            if str(i.get("accurate_line_id")) == LINE_NC
        ),
        None,
    )
    if other and str(other.get("item_name") or "").strip():
        assert tasks[1]["item_name"] == str(other.get("item_name")).strip()
        assert tasks[1]["item_name"] != tasks[0]["item_name"] or expected_name == str(
            other.get("item_name")
        ).strip()
    # Line tidak ada → jangan mengarang nama
    assert tasks[2]["item_name"] == ""
    assert "item_name" not in tasks[2]["ref"] or not tasks[2]["ref"].get("item_name")
    # Enrichment read-only — tidak menulis collection tugas.
    assert db["operational_tasks"].find_one({}) == before_doc


def test_designer_po_scoped_item_name_from_print_lines(db: Database) -> None:
    """Designer PO-scoped (tanpa line_id) → Nama dari print_lines, bukan first-line arbitrer."""
    po = _seed_po(db)
    print_line = next(
        i
        for i in po["header"]["items"]
        if str(i.get("accurate_line_id")) == LINE_PRINT
    )
    expected = str(print_line.get("item_name") or "").strip()
    assert expected
    # Tambah Cetak kedua agar multi-item tidak boleh jadi first-line tunggal.
    second_name = "Barang Simulasi Cetak Kedua"
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {
            "$push": {
                "header.items": {
                    "accurate_line_id": "106320",
                    "item_code": "BRG-SIM-003",
                    "item_name": second_name,
                    "qty": 500,
                    "unit": "PCS",
                    "process_kind": "Cetak",
                }
            }
        },
    )
    design = {
        "task_key": f"task:design:po:{PO_ID}",
        "task_type": "design",
        "scope": "po",
        "ref": {"accurate_po_id": PO_ID, "po_no": "PO/18/11/2026/000010"},
        "context": {"print_line_count": 2},
    }
    missing_po = {
        "task_key": "task:design:po:OTHER-BRANCH",
        "task_type": "design",
        "scope": "po",
        "ref": {"accurate_po_id": "PO-NOT-IN-DB"},
    }
    enrich.enrich_operational_tasks(db, [design, missing_po])
    names = [r["item_name"] for r in design["item_lines"]]
    assert expected in names
    assert second_name in names
    assert "\n" in design["item_name"]
    assert expected in design["item_name"]
    assert second_name in design["item_name"]
    # Bukan hanya first-line: kedua nama hadir.
    assert design["item_name"] != expected
    # Cross-branch / PO absen → tidak mengarang nama dari PO lain.
    assert missing_po["item_name"] == ""
    assert missing_po["item_lines"] == []


def test_calendar_sort_and_no_utc_shift() -> None:
    dates = enrich.unique_sorted_ship_dates(
        ["27/11/2026", "25/11/2026", "25/11/2026", "01/12/2026"]
    )
    assert dates == ["25/11/2026", "27/11/2026", "01/12/2026"]


def test_sj_without_ship_date_yields_empty(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-EMPTY", ship_date="25/11/2026"))
    # Paksa kosong setelah normalisasi (simulasikan dokumen tanpa ship_date).
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": "SJ-EMPTY"}, {"$set": {"ship_date": ""}}
    )
    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    assert enrich.is_active_delivery_note(notes[0]) is True
    assert enrich.ship_dates_by_line_id(notes) == {}


def test_multi_item_sj_maps_all_lines(db: Database) -> None:
    _seed_po(db)
    payload = _sj(sj_id="SJ-MULTI", ship_date="25/11/2026", line_id=LINE_PRINT)
    payload["detailItem"].append(
        {
            "id": "SJL-MULTI-NC",
            "salesOrderDetailId": LINE_NC,
            "itemNo": "BRG-NC",
            "quantity": 5,
            "itemUnitName": "PACK",
        }
    )
    deliveries.upsert_delivery_note(db, payload)
    by_line = enrich.ship_dates_by_line_id(
        enrich.list_active_delivery_notes_for_po(db, PO_ID)
    )
    assert by_line[LINE_PRINT] == ["25/11/2026"]
    assert by_line[LINE_NC] == ["25/11/2026"]


def test_sj_revision_uses_latest_active_ship_date(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-REV", ship_date="20/11/2026"))
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-REV", ship_date="29/11/2026"))
    by_line = enrich.ship_dates_by_line_id(
        enrich.list_active_delivery_notes_for_po(db, PO_ID)
    )
    assert by_line[LINE_PRINT] == ["29/11/2026"]
    note = deliveries.get_delivery_note(db, "SJ-REV")
    assert note is not None
    assert note.get("ship_date") == "29/11/2026"
    # Lifecycle note: tidak ada status cancel pada delivery_notes —
    # SoT inactive/replace = superseded_by (sudah diuji terpisah).


def test_inactive_lifecycle_is_superseded_only(db: Database) -> None:
    """Canceled loading ≠ canceled SJ note; inactive display = superseded_by."""
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-LIVE", ship_date="25/11/2026"))
    note = deliveries.get_delivery_note(db, "SJ-LIVE")
    assert enrich.is_active_delivery_note(note) is True
    # Field cancel pada loading event tidak memengaruhi note aktif.
    assert enrich.is_active_delivery_note(
        {**(note or {}), "cancelled_at": "x"}  # field asing diabaikan
    ) is True
    assert enrich.is_active_delivery_note(
        {**(note or {}), "superseded_by": "SJ-NEW"}
    ) is False


def test_task_po_level_collects_unique_dates(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-A", ship_date="25/11/2026"))
    deliveries.upsert_delivery_note(
        db, _sj(sj_id="SJ-B", ship_date="27/11/2026", line_id=LINE_NC)
    )
    tasks = [
        {
            "task_key": "t-po",
            "task_type": "penjadwalan",
            "ref": {"accurate_po_id": PO_ID},
        }
    ]
    enrich.enrich_operational_tasks(db, tasks)
    assert tasks[0]["ship_dates"] == ["25/11/2026", "27/11/2026"]


def test_schedule_sj_lookup_once_per_po(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    calls = {"list": 0}
    real = deliveries.list_delivery_notes

    def _spy(*a, **k):
        calls["list"] += 1
        return real(*a, **k)

    monkeypatch.setattr(deliveries, "list_delivery_notes", _spy)
    monkeypatch.setattr(enrich.deliveries, "list_delivery_notes", _spy)

    notes = enrich.list_active_delivery_notes_for_po(db, PO_ID)
    by_line = enrich.ship_dates_by_line_id(notes)
    outlet = enrich.outlet_name_from_po(po)
    # Dua line Cetak dari sample? Sample punya 1 Cetak + 1 Non-Cetak.
    print_lines = [
        i
        for i in po["header"]["items"]
        if i.get("process_kind") == "Cetak" and i.get("accurate_line_id")
    ]
    assert len(print_lines) >= 1
    for item in print_lines:
        scheduling_service.line_schedule_state(
            db,
            po,
            str(item["accurate_line_id"]),
            outlet_name=outlet,
            ship_dates=by_line.get(str(item["accurate_line_id"]), []),
        )
    assert calls["list"] == 1


def test_task_batch_enrich_query_count(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    list_calls = {"n": 0}
    get_po_calls = {"n": 0}
    get_sj_calls = {"n": 0}
    real_list = deliveries.list_delivery_notes
    real_get_note = deliveries.get_delivery_note
    real_get_po = get_by_accurate_id

    def _list(*a, **k):
        list_calls["n"] += 1
        return real_list(*a, **k)

    def _note(*a, **k):
        get_sj_calls["n"] += 1
        return real_get_note(*a, **k)

    def _po(*a, **k):
        get_po_calls["n"] += 1
        return real_get_po(*a, **k)

    monkeypatch.setattr(enrich.deliveries, "list_delivery_notes", _list)
    monkeypatch.setattr(enrich.deliveries, "get_delivery_note", _note)
    monkeypatch.setattr(enrich, "get_by_accurate_id", _po)

    tasks = [
        {
            "task_key": f"t-{i}",
            "ref": {"accurate_po_id": PO_ID, "accurate_line_id": LINE_PRINT},
        }
        for i in range(8)
    ]
    enrich.enrich_operational_tasks(db, tasks)
    # Batch: 1 PO fetch, 1 list SJ untuk PO — bukan 8×.
    assert get_po_calls["n"] == 1
    assert list_calls["n"] == 1
    assert get_sj_calls["n"] == 0  # tidak ada ref SJ langsung


def test_task_does_not_inherit_unrelated_sj_customer(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    tasks = [
        {
            "task_key": "orphan-sj",
            "ref": {"accurate_delivery_note_id": "SJ-DOES-NOT-EXIST"},
        }
    ]
    enrich.enrich_operational_tasks(db, tasks)
    assert tasks[0]["outlet_name"] == ""
    assert tasks[0]["ship_dates"] == []


def test_auth_order_enrich_after_empty_filter(db: Database) -> None:
    """Enrich pada list kosong tidak mengisi data silang."""
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-1", ship_date="25/11/2026"))
    out = enrich.enrich_operational_tasks(db, [])
    assert out == []
