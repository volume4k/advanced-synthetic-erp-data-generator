"""Create goods receipt tool for SAP Fiori."""

from __future__ import annotations

import re
from typing import Literal

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import RuntimeDelay, noop_delay, runtime_delay_callback

MATERIAL_DOCUMENT_LINK_PATTERN = re.compile(r"Materialbeleg\s+(\d+)/?")
MATERIAL_VALUATION_LOCK_PATTERN = re.compile(
    r"Bewertungsdaten\s+zum\s+Material\s+(?P<material>\S+)\s+sind\s+von\s+Benutzer\s+(?P<user>\S+)\s+gesperrt",
    re.IGNORECASE,
)
NO_SELECTABLE_POSITION_PATTERN = re.compile(
    r"Beleg\s+\d+\s+enthält keine wählbare Position"
)
MATERIAL_VALUATION_LOCK_RETRIES = 3
MATERIAL_VALUATION_LOCK_RETRY_DELAY_MS = 30_000
STORAGE_LOCATION_CELL_SELECTOR = '[id*="idStorageLocation_inputCell"][id$="-inner"]'
STORAGE_LOCATION_OPTION_SELECTOR = 'span[id*="idStorageLocation"]'
StorageLocation = Literal["Finished Goods", "Trading Goods", "Miscellaneous", "Returns"]


class CreateGoodsReceiptInput(BaseModel):
    """Input values for posting a goods receipt against a purchase order."""

    model_config = ConfigDict(extra="forbid")

    purchase_order: str
    storage_location: StorageLocation


class SapGoodsReceiptFlow:
    """Recorded SAP Fiori goods receipt flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def create(self, params: CreateGoodsReceiptInput) -> dict[str, str]:
        page = self._page

        self._delay("app_open_search", 1.5)
        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill(
            "Wareneingang zu Einkaufsbeleg buchen"
        )
        page.get_by_role("gridcell", name="Wareneingang zu Einkaufsbeleg").locator(
            "b"
        ).click(retry_on_next_wait=True)

        purchase_order = page.get_by_role("textbox", name="Einkaufsbeleg")
        purchase_order.wait_for(state="visible")
        self._delay("form_section_fill", 1.0)
        purchase_order.click()
        purchase_order.fill(params.purchase_order)
        # SAP can expose this field before it reliably accepts input; retry once before committing.
        if purchase_order.input_value() != params.purchase_order:
            page.wait_for_timeout(500)
            purchase_order.fill(params.purchase_order)
        current_purchase_order = purchase_order.input_value()
        if current_purchase_order != params.purchase_order:
            raise ToolExecutionError(
                f"Failed to fill purchase order field with '{params.purchase_order}'; current value is "
                f"'{current_purchase_order}'"
            )
        purchase_order.press("Enter")
        self._raise_if_no_selectable_position(page, params.purchase_order)

        page.locator(STORAGE_LOCATION_CELL_SELECTOR).first.click()
        page.locator(
            STORAGE_LOCATION_OPTION_SELECTOR, has_text=params.storage_location
        ).first.click()
        self._delay("review_save_post", 1.5)
        material_document = self._post_with_material_lock_retry(page, params)

        page.get_by_role("button", name="OK").click()
        return {
            "material_document": material_document,
            "purchase_order": params.purchase_order,
            "storage_location": params.storage_location,
        }

    def _raise_if_no_selectable_position(self, page, purchase_order: str) -> None:
        message = page.get_by_text(NO_SELECTABLE_POSITION_PATTERN).first
        try:
            message.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return

        raise ToolExecutionError(
            f"Purchase order '{purchase_order}' contains no selectable goods receipt position: {message.inner_text()}"
        )

    def _post_with_material_lock_retry(self, page, params: CreateGoodsReceiptInput) -> str:
        last_lock: tuple[str, str, str] | None = None
        for attempt in range(1, MATERIAL_VALUATION_LOCK_RETRIES + 1):
            page.get_by_role("button", name="Buchen", exact=True).click()
            success_dialog = page.locator('[role="dialog"]', has_text="Materialbeleg").first
            try:
                success_dialog.wait_for(state="visible", recover_fiori_messages=False)
                return _extract_material_document(success_dialog.inner_text())
            except PlaywrightTimeoutError as exc:
                lock = self._material_valuation_lock_message(page)
                if lock is None:
                    raise
                last_lock = lock
                if attempt < MATERIAL_VALUATION_LOCK_RETRIES:
                    page.wait_for_timeout(MATERIAL_VALUATION_LOCK_RETRY_DELAY_MS)
                    continue
                material_id, locking_user, message_text = lock
                raise ToolExecutionError(
                    "Material valuation data stayed locked while posting goods receipt; "
                    f"purchase_order={params.purchase_order}; "
                    f"material_id={material_id}; "
                    f"locking_user={locking_user}; "
                    f"attempts={MATERIAL_VALUATION_LOCK_RETRIES}; "
                    f"sap_message={message_text}"
                ) from exc
        assert last_lock is not None
        raise AssertionError("unreachable material valuation lock retry state")

    def _material_valuation_lock_message(self, page) -> tuple[str, str, str] | None:
        messages = page.handle_messages()
        for message in messages:
            message_text = str(getattr(message, "text", ""))
            match = MATERIAL_VALUATION_LOCK_PATTERN.search(message_text)
            if match is not None:
                return match.group("material"), match.group("user"), message_text
        return None


def run_create_goods_receipt(
    context: ExecutionContext,
    params: CreateGoodsReceiptInput,
) -> ToolResult:
    page = context.get_fiori_page()
    goods_receipt_data = SapGoodsReceiptFlow(page, delay=runtime_delay_callback(context)).create(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            "returned_objects": [
                returned_object("material_document", material_document_number=goods_receipt_data["material_document"])
            ],
            **goods_receipt_data,
        },
    )


CREATE_GOODS_RECEIPT_TOOL = ToolSpec(
    name="fiori.create_goods_receipt",
    input_model=CreateGoodsReceiptInput,
    run=run_create_goods_receipt,
)


def _extract_material_document(message: str) -> str:
    match = MATERIAL_DOCUMENT_LINK_PATTERN.search(message)
    if match is None:
        raise ValueError(
            f"Could not extract material document number from success link: {message}"
        )
    return match.group(1)
