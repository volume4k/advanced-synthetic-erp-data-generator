from __future__ import annotations

from datetime import UTC, datetime

from erp_sap_export.se16 import AbapListItem, parse_abap_list
from erp_sap_export.specs import (
    SelectionRange,
    cdhdr_selection,
    cdpos_requests_from_cdhdr,
    p2p_batched_requests_from_registry,
    p2p_requests_from_registry,
)


def test_parse_abap_list_maps_header_positions_to_rows() -> None:
    items = [
        AbapListItem(text="MANDANT", x=72, y=193),
        AbapListItem(text="OBJECTCLAS", x=136, y=193),
        AbapListItem(text="OBJECTID", x=280, y=193),
        AbapListItem(text="CHANGENR", x=1024, y=193),
        AbapListItem(text="USERNAME", x=1128, y=193),
        AbapListItem(text="UDATE", x=1240, y=193),
        AbapListItem(text="204", x=72, y=247),
        AbapListItem(text="EINKBELEG", x=136, y=247),
        AbapListItem(text="4500000138", x=280, y=247),
        AbapListItem(text="0000734604", x=1024, y=247),
        AbapListItem(text="LEARN-801", x=1128, y=247),
        AbapListItem(text="05/28/2026", x=1240, y=247),
    ]

    assert parse_abap_list(items) == [
        {
            "MANDANT": "204",
            "OBJECTCLAS": "EINKBELEG",
            "OBJECTID": "4500000138",
            "CHANGENR": "0000734604",
            "USERNAME": "LEARN-801",
            "UDATE": "05/28/2026",
        }
    ]


def test_cdhdr_selection_uses_user_range_and_same_day_time_range() -> None:
    ranges = cdhdr_selection(
        start=datetime(2026, 5, 28, 16, 0, tzinfo=UTC),
        end=datetime(2026, 5, 28, 17, 15, 30, tzinfo=UTC),
        user_from="LEARN-800",
        user_to="LEARN-899",
    )

    assert ranges == [
        SelectionRange("USERNAME", "LEARN-800", "LEARN-899"),
        SelectionRange("UDATE", "05/28/2026", "05/28/2026"),
        SelectionRange("UTIME", "18:00:00", "19:15:30"),
    ]


def test_cdhdr_selection_omits_time_range_across_multiple_days() -> None:
    ranges = cdhdr_selection(
        start=datetime(2026, 5, 28, 20, 0, tzinfo=UTC),
        end=datetime(2026, 5, 29, 3, 15, 30, tzinfo=UTC),
        user_from="LEARN-800",
        user_to="LEARN-899",
    )

    assert ranges == [
        SelectionRange("USERNAME", "LEARN-800", "LEARN-899"),
        SelectionRange("UDATE", "05/28/2026", "05/29/2026"),
    ]


def test_cdpos_requests_are_derived_from_cdhdr_composite_keys() -> None:
    rows = [
        {"OBJECTCLAS": "BANF", "OBJECTID": "0010000172", "CHANGENR": "0000734602"},
        {"OBJECTCLAS": "EINKBELEG", "OBJECTID": "4500000138", "CHANGENR": "0000734604"},
        {"OBJECTCLAS": "BANF", "OBJECTID": "0010000172", "CHANGENR": "0000734602"},
    ]

    requests = cdpos_requests_from_cdhdr(rows)

    assert [(item.table, item.selection) for item in requests] == [
        (
            "CDPOS",
            [
                SelectionRange("OBJECTCLAS", "BANF"),
                SelectionRange("OBJECTID", "0010000172"),
                SelectionRange("CHANGENR", "0000734602"),
            ],
        ),
        (
            "CDPOS",
            [
                SelectionRange("OBJECTCLAS", "EINKBELEG"),
                SelectionRange("OBJECTID", "4500000138"),
                SelectionRange("CHANGENR", "0000734604"),
            ],
        ),
    ]


def test_p2p_requests_are_built_from_object_registry_keys() -> None:
    registry_entries = [
        {"object_type": "purchase_requisition", "keys": {"pr_number": "10000091"}},
        {"object_type": "purchase_order", "keys": {"po_number": "4500000057"}},
        {
            "object_type": "material_document",
            "keys": {"material_document_number": "5000000054", "material_document_year": "2026"},
        },
        {"object_type": "supplier_invoice", "keys": {"invoice_number": "5105600133", "fiscal_year": "2026"}},
        {"object_type": "payment_document", "keys": {"payment_document_number": "1500000028", "fiscal_year": "2026"}},
    ]
    trace_steps = {
        "unused": {"inputs": {"company_code": "US00"}},
    }

    requests = p2p_requests_from_registry(registry_entries, trace_steps, default_company_code="US00")

    assert [(item.table, item.selection) for item in requests] == [
        ("EBAN", [SelectionRange("BANFN", "10000091")]),
        ("EKKO", [SelectionRange("EBELN", "4500000057")]),
        ("EKPO", [SelectionRange("EBELN", "4500000057")]),
        ("MKPF", [SelectionRange("MBLNR", "5000000054"), SelectionRange("MJAHR", "2026")]),
        ("MSEG", [SelectionRange("MBLNR", "5000000054"), SelectionRange("MJAHR", "2026")]),
        ("RBKP", [SelectionRange("BELNR", "5105600133"), SelectionRange("GJAHR", "2026")]),
        ("RSEG", [SelectionRange("BELNR", "5105600133"), SelectionRange("GJAHR", "2026")]),
        ("BKPF", [SelectionRange("BELNR", "1500000028"), SelectionRange("BUKRS", "US00"), SelectionRange("GJAHR", "2026")]),
        ("BSEG", [SelectionRange("BELNR", "1500000028"), SelectionRange("BUKRS", "US00"), SelectionRange("GJAHR", "2026")]),
    ]


def test_p2p_batched_requests_use_object_key_ranges() -> None:
    registry_entries = [
        {"object_type": "purchase_requisition", "keys": {"pr_number": "10000091"}},
        {"object_type": "purchase_requisition", "keys": {"pr_number": "10000094"}},
        {"object_type": "purchase_order", "keys": {"po_number": "4500000057"}},
        {"object_type": "purchase_order", "keys": {"po_number": "4500000060"}},
        {"object_type": "supplier_invoice", "keys": {"invoice_number": "5105600133", "fiscal_year": "2026"}},
        {"object_type": "supplier_invoice", "keys": {"invoice_number": "5105600140", "fiscal_year": "2026"}},
    ]

    requests = p2p_batched_requests_from_registry(registry_entries, {}, default_company_code="US00")

    assert [(item.table, item.selection) for item in requests] == [
        ("EBAN", [SelectionRange("BANFN", "10000091", "10000094")]),
        ("EKKO", [SelectionRange("EBELN", "4500000057", "4500000060")]),
        ("EKPO", [SelectionRange("EBELN", "4500000057", "4500000060")]),
        ("RBKP", [SelectionRange("BELNR", "5105600133", "5105600140"), SelectionRange("GJAHR", "2026")]),
        ("RSEG", [SelectionRange("BELNR", "5105600133", "5105600140"), SelectionRange("GJAHR", "2026")]),
    ]


def test_p2p_batched_requests_split_wide_numeric_prefix_gaps() -> None:
    registry_entries = [
        {"object_type": "material_document", "keys": {"material_document_number": "5000000133"}},
        {"object_type": "material_document", "keys": {"material_document_number": "5000000180"}},
        {"object_type": "scrap_material_document", "keys": {"material_document_number": "4900038018"}},
        {"object_type": "stock_release_material_document", "keys": {"material_document_number": "4900038020"}},
    ]

    requests = p2p_batched_requests_from_registry(registry_entries, {}, default_company_code="US00")

    assert [(item.table, item.selection) for item in requests] == [
        ("MKPF", [SelectionRange("MBLNR", "5000000133", "5000000180")]),
        ("MSEG", [SelectionRange("MBLNR", "5000000133", "5000000180")]),
        ("MKPF", [SelectionRange("MBLNR", "4900038018", "4900038020")]),
        ("MSEG", [SelectionRange("MBLNR", "4900038018", "4900038020")]),
    ]


def test_p2p_batched_requests_chunk_large_ranges() -> None:
    registry_entries = [
        {"object_type": "purchase_requisition", "keys": {"pr_number": str(number)}}
        for number in range(10000172, 10000197)
    ]

    requests = p2p_batched_requests_from_registry(
        registry_entries,
        {},
        default_company_code="US00",
        max_keys_per_batch=20,
    )

    assert [(item.table, item.selection) for item in requests] == [
        ("EBAN", [SelectionRange("BANFN", "10000172", "10000191")]),
        ("EBAN", [SelectionRange("BANFN", "10000192", "10000196")]),
    ]


def test_p2p_batched_requests_sort_numeric_bounds_numerically() -> None:
    registry_entries = [
        {"object_type": "purchase_requisition", "keys": {"pr_number": "1000009"}},
        {"object_type": "purchase_requisition", "keys": {"pr_number": "10000010"}},
    ]

    requests = p2p_batched_requests_from_registry(registry_entries, {}, default_company_code="US00")

    assert [(item.table, item.selection) for item in requests] == [
        ("EBAN", [SelectionRange("BANFN", "1000009", "10000010")]),
    ]
