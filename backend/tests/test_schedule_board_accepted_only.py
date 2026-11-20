"""Jadwal Produksi board: accepted-only + pagination per PO-line Cetak + KPI global."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_service
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, ACCEPTANCE_PENDING
from tests.po_accept_helpers import mark_po_accepted

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NC = "106319"


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _seed_po(db: Database, *, accurate_id: str = PO_ID) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id) if str(accurate_id).isdigit() else accurate_id
    payload["number"] = f"PO/TEST/{accurate_id}"
    upsert_sales_order(db, normalize_sales_order(payload))
    return get_by_accurate_id(db, str(accurate_id))


def _set_acceptance(db: Database, accurate_id: str, status: str) -> None:
    db["accurate_sales_orders"].update_one(
        {"accurate_id": str(accurate_id)},
        {
            "$set": {
                "operational.acceptance.status": status,
                "operational.acceptance.accepted_by": "kp@eternal.local"
                if status == ACCEPTANCE_ACCEPTED
                else None,
            }
        },
    )


def _bulk_accepted_pos(
    db: Database,
    n: int,
    *,
    lines_per_po: int = 1,
    start: int = 200000,
    mixed_non_cetak: bool = False,
) -> None:
    docs: list[dict] = []
    for i in range(n):
        aid = str(start + i)
        items: list[dict] = []
        for j in range(lines_per_po):
            items.append(
                {
                    "accurate_line_id": f"{aid}-L{j}",
                    "item_code": f"CUP-{j}",
                    "item_name": f"Cup line {j}",
                    "qty": 100.0,
                    "unit": "PCS",
                    "process_kind": "Cetak",
                    "color": "PUTIH",
                }
            )
        if mixed_non_cetak:
            items.append(
                {
                    "accurate_line_id": f"{aid}-NC",
                    "item_code": "LID",
                    "item_name": "Lid Non-Cetak",
                    "qty": 50.0,
                    "unit": "PCS",
                    "process_kind": "Non-Cetak",
                    "color": "NATURAL",
                }
            )
        docs.append(
            {
                "accurate_id": aid,
                "po_no": f"PO/BULK/{aid}",
                "origin": "accurate",
                "updated_at": f"2026-11-{(i % 28) + 1:02d}T10:00:00Z",
                "header": {
                    "customer": f"Outlet {aid}",
                    "items": items,
                },
                "operational": {
                    "acceptance": {
                        "status": ACCEPTANCE_ACCEPTED,
                        "accepted_by": "kp@eternal.local",
                    },
                    "inbox_status": "PO Diterima",
                },
            }
        )
    if docs:
        db["accurate_sales_orders"].insert_many(docs)


def test_board_shows_accepted_cetak_only(db: Database) -> None:
    _seed_po(db)
    mark_po_accepted(db)
    board = scheduling_service.build_accepted_schedule_board(db, page_size=100)
    assert board["accepted_only"] is True
    assert board["total"] >= 1
    assert all(l["process_kind"] == "Cetak" for l in board["items"])
    assert all(l["accurate_po_id"] == PO_ID for l in board["items"])
    assert LINE_PRINT in {l["accurate_line_id"] for l in board["items"]}
    assert LINE_NC not in {l["accurate_line_id"] for l in board["items"]}
    assert board["kpi"]["print_line_count"] == board["total"]
    assert "outlet_name" in board["items"][0]
    assert "paint_color" in board["items"][0]
    assert "ship_dates" in board["items"][0]


def test_board_hides_pending_rejected_and_missing(db: Database) -> None:
    _seed_po(db, accurate_id="90001")
    _set_acceptance(db, "90001", ACCEPTANCE_PENDING)
    _seed_po(db, accurate_id="90002")
    _set_acceptance(db, "90002", "rejected")
    _seed_po(db, accurate_id="90003")
    board = scheduling_service.build_accepted_schedule_board(db)
    ids = {l["accurate_po_id"] for l in board["items"]}
    assert "90001" not in ids
    assert "90002" not in ids
    assert "90003" not in ids
    assert board["total"] == 0


def test_board_accepted_non_cetak_only_hidden(db: Database) -> None:
    po = _seed_po(db, accurate_id="90010")
    mark_po_accepted(db, "90010")
    items = [
        i
        for i in (po["header"]["items"] or [])
        if i.get("process_kind") != "Cetak"
    ]
    assert items
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "90010"},
        {"$set": {"header.items": items}},
    )
    board = scheduling_service.build_accepted_schedule_board(db)
    assert board["total"] == 0


def test_board_mixed_po_only_cetak_lines(db: Database) -> None:
    _seed_po(db)
    mark_po_accepted(db)
    board = scheduling_service.build_accepted_schedule_board(db)
    assert {l["process_kind"] for l in board["items"]} == {"Cetak"}


def test_board_pagination_population_not_inbox_mixed(db: Database) -> None:
    for i, pid in enumerate(("91001", "91002", "91003")):
        _seed_po(db, accurate_id=pid)
        _set_acceptance(db, pid, ACCEPTANCE_PENDING)
        db["accurate_sales_orders"].update_one(
            {"accurate_id": pid},
            {"$set": {"updated_at": f"2026-11-2{i}T12:00:00Z"}},
        )
    _seed_po(db, accurate_id="91099")
    mark_po_accepted(db, "91099")
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "91099"},
        {"$set": {"updated_at": "2026-11-01T12:00:00Z"}},
    )
    board = scheduling_service.build_accepted_schedule_board(db)
    ids = {l["accurate_po_id"] for l in board["items"]}
    assert "91099" in ids
    assert "91001" not in ids


def test_detail_rejects_non_accepted(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _set_acceptance(db, PO_ID, ACCEPTANCE_PENDING)
    _seed_owner(db)
    _login(app_client, "owner@eternal.local")
    res = app_client.get(f"/api/scheduling/{PO_ID}")
    assert res.status_code == 409


def test_board_http_accepted_only(app_client: TestClient, db: Database) -> None:
    _seed_po(db, accurate_id="92001")
    _set_acceptance(db, "92001", ACCEPTANCE_PENDING)
    _seed_po(db, accurate_id="92002")
    mark_po_accepted(db, "92002")
    _seed_owner(db)
    _login(app_client, "owner@eternal.local")
    res = app_client.get("/api/scheduling/board")
    assert res.status_code == 200
    body = res.json()
    assert body["accepted_only"] is True
    ids = {l["accurate_po_id"] for l in body["items"]}
    assert "92002" in ids
    assert "92001" not in ids
    assert body["kpi"]["print_line_count"] == body["total"]
    assert body["page"] == 1
    assert body["pageSize"] == 100


def test_board_branch_isolation(app_client: TestClient, db: Database) -> None:
    _seed_po(db, accurate_id="93001")
    _set_acceptance(db, "93001", ACCEPTANCE_ACCEPTED)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "93001"}, {"$set": {"branch_id": "BR-A"}}
    )
    _seed_po(db, accurate_id="93002")
    _set_acceptance(db, "93002", ACCEPTANCE_ACCEPTED)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "93002"}, {"$set": {"branch_id": "BR-B"}}
    )
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("branch@eternal.local"),
            "display_name": "Branch User",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "production-head",
            "is_active": True,
            "branch_ids": ["BR-A"],
        }
    )
    _login(app_client, "branch@eternal.local")
    res = app_client.get("/api/scheduling/board")
    assert res.status_code == 200
    ids = {l["accurate_po_id"] for l in res.json()["items"]}
    assert "93001" in ids
    assert "93002" not in ids


def test_board_zero_and_one(db: Database) -> None:
    empty = scheduling_service.build_accepted_schedule_board(db)
    assert empty["total"] == 0
    assert empty["kpi"]["print_line_count"] == 0
    assert empty["items"] == []
    assert empty["total_pages"] == 1

    _bulk_accepted_pos(db, 1, start=300000)
    one = scheduling_service.build_accepted_schedule_board(db, page_size=100)
    assert one["total"] == 1
    assert len(one["items"]) == 1
    assert one["kpi"]["total_qty_so"] == 100.0


def test_board_no_500_cap_501_and_750(db: Database) -> None:
    _bulk_accepted_pos(db, 501, start=310000)
    board_501 = scheduling_service.build_accepted_schedule_board(
        db, page=1, page_size=100
    )
    assert board_501["total"] == 501
    assert board_501["kpi"]["print_line_count"] == 501
    assert board_501["total_pages"] == 6
    assert len(board_501["items"]) == 100

    # Semua baris terjangkau tanpa cap PO.
    seen: set[str] = set()
    for p in range(1, board_501["total_pages"] + 1):
        page = scheduling_service.build_accepted_schedule_board(
            db, page=p, page_size=100
        )
        assert page["kpi"] == board_501["kpi"]  # KPI global stabil antar halaman
        for row in page["items"]:
            key = f"{row['accurate_po_id']}:{row['accurate_line_id']}"
            assert key not in seen
            seen.add(key)
    assert len(seen) == 501

    _bulk_accepted_pos(db, 249, start=310000 + 501)  # total 750
    board_750 = scheduling_service.build_accepted_schedule_board(
        db, page=1, page_size=100
    )
    assert board_750["total"] == 750
    assert board_750["kpi"]["print_line_count"] == 750
    assert board_750["kpi"]["total_qty_so"] == 750 * 100.0
    last = scheduling_service.build_accepted_schedule_board(
        db, page=board_750["total_pages"], page_size=100
    )
    assert last["kpi"] == board_750["kpi"]
    assert len(last["items"]) == 50  # 750 % 100


def test_board_multi_line_and_mixed_non_cetak(db: Database) -> None:
    _bulk_accepted_pos(db, 100, start=320000, lines_per_po=2, mixed_non_cetak=True)
    board = scheduling_service.build_accepted_schedule_board(
        db, page=1, page_size=200
    )
    # 100 PO × 2 Cetak — Non-Cetak tidak dihitung.
    assert board["total"] == 200
    assert board["kpi"]["print_line_count"] == 200
    assert board["kpi"]["total_qty_so"] == 200 * 100.0
    assert all(r["process_kind"] == "Cetak" for r in board["items"])


def test_board_page_out_of_range(db: Database) -> None:
    _bulk_accepted_pos(db, 5, start=330000)
    board = scheduling_service.build_accepted_schedule_board(
        db, page=99, page_size=100
    )
    assert board["total"] == 5
    assert board["items"] == []
    assert board["kpi"]["print_line_count"] == 5


def test_board_search_before_pagination(db: Database) -> None:
    _bulk_accepted_pos(db, 50, start=340000)
    # Override satu PO agar match q unik.
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "340010"},
        {"$set": {"po_no": "PO/UNIQUE-SEARCH/ZZZ", "header.customer": "Outlet ZZZ"}},
    )
    board = scheduling_service.build_accepted_schedule_board(
        db, q="UNIQUE-SEARCH", page=1, page_size=10
    )
    assert board["total"] == 1
    assert board["items"][0]["po_no"] == "PO/UNIQUE-SEARCH/ZZZ"
    assert board["kpi"]["print_line_count"] == 1


def test_board_header_filter_and_sort_across_pages(db: Database) -> None:
    """Filter/sort server sebelum skip/limit — melewati batas halaman."""
    _bulk_accepted_pos(db, 120, start=350000)
    for aid, color in (("350005", "Merah"), ("350090", "Biru")):
        doc = db["accurate_sales_orders"].find_one({"accurate_id": aid})
        assert doc is not None
        items = list(doc["header"]["items"])
        items[0] = {**items[0], "color": color}
        db["accurate_sales_orders"].update_one(
            {"accurate_id": aid},
            {"$set": {"header.customer": "Outlet Alpha", "header.items": items}},
        )

    filtered = scheduling_service.build_accepted_schedule_board(
        db,
        page=1,
        page_size=10,
        column_filters={"outletName": ["Outlet Alpha"]},
        sort_by="paintColor",
        sort_dir="asc",
    )
    assert filtered["total"] == 2
    assert filtered["kpi"]["print_line_count"] == 2
    assert len(filtered["items"]) == 2
    assert filtered["items"][0]["paint_color"] == "Biru"
    assert filtered["items"][1]["paint_color"] == "Merah"
    assert "Outlet Alpha" in filtered["filter_facets"]["outletName"]

    # Reset filter → dataset penuh kembali.
    reset = scheduling_service.build_accepted_schedule_board(
        db, page=1, page_size=10
    )
    assert reset["total"] == 120
    assert reset["kpi"]["print_line_count"] == 120


def test_board_filter_empty_selection(db: Database) -> None:
    _bulk_accepted_pos(db, 5, start=360000)
    board = scheduling_service.build_accepted_schedule_board(
        db, column_filters={"outletName": []}
    )
    assert board["total"] == 0
    assert board["items"] == []
    assert board["kpi"]["print_line_count"] == 0
