"""Page-object helpers for the local Fiori fixture app."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from erp_trace_executor.errors import ToolExecutionError


class FixtureFioriPage:
    """Wraps selectors and flows for the test fixture app."""

    def __init__(self, page: Page) -> None:
        self._page = page

    def goto(self, base_url: str) -> None:
        self._page.goto(base_url)

    def login(self, username: str, password: str) -> None:
        self._page.get_by_test_id("username").fill(username)
        self._page.get_by_test_id("password").fill(password)
        self._page.get_by_test_id("login-submit").click()
        expect(self._page.get_by_test_id("session-user")).to_have_text(username)

    def ensure_logged_in(self, expected_username: str) -> None:
        if not self._page.get_by_test_id("session-shell").is_visible():
            raise ToolExecutionError("The current browser session is not logged in")
        expect(self._page.get_by_test_id("session-user")).to_have_text(expected_username)

    def create_order(self, item_name: str, quantity: int) -> dict[str, str | int]:
        self._page.get_by_test_id("item-name").fill(item_name)
        self._page.get_by_test_id("item-quantity").fill(str(quantity))
        self._page.get_by_test_id("order-submit").click()
        summary = self._page.get_by_test_id("latest-order")
        expect(summary).to_have_text(f"{item_name}:{quantity}")
        order_count = int(self._page.get_by_test_id("order-count").inner_text())
        return {
            "item_name": item_name,
            "quantity": quantity,
            "order_count": order_count,
            "latest_order": summary.inner_text(),
        }

    def create_purchase_requisition(
        self,
        *,
        material: str,
        quantity: int,
        valuation_price: float,
        currency: str,
        price_unit: int,
        delivery_date: str,
        plant: str,
        purchasing_group: str,
        purchasing_organization: str,
        company_code: str,
    ) -> dict[str, str | int]:
        self._page.get_by_test_id("pr-material").fill(material)
        self._page.get_by_test_id("pr-quantity").fill(str(quantity))
        self._page.get_by_test_id("pr-valuation-price").fill(str(valuation_price))
        self._page.get_by_test_id("pr-currency").fill(currency)
        self._page.get_by_test_id("pr-price-unit").fill(str(price_unit))
        self._page.get_by_test_id("pr-delivery-date").fill(delivery_date)
        self._page.get_by_test_id("pr-plant").fill(plant)
        self._page.get_by_test_id("pr-purchasing-group").fill(purchasing_group)
        self._page.get_by_test_id("pr-purchasing-organization").fill(purchasing_organization)
        self._page.get_by_test_id("pr-company-code").fill(company_code)
        self._page.get_by_test_id("pr-cart").click()
        self._page.get_by_role("button", name="Bestellen").click()
        requisition_link = self._page.locator("#idPRNoLinkId")
        expect(requisition_link).to_be_visible()
        return {
            "purchase_requisition": requisition_link.inner_text(),
            "material": material,
            "quantity": quantity,
        }


class PurchaseRequisitionPage:
    """Wraps SAP Fiori purchase requisition creation flow."""

    CREATE_URL = (
        "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"
        "#PurchaseRequisition-create?mode=create&sap-ui-tech-hint=UI5&/Search"
    )

    def __init__(self, page: Page) -> None:
        self._page = page

    def goto(self) -> None:
        self._page.goto(self.CREATE_URL)
        self._page.wait_for_load_state("load")

    def create(
        self,
        *,
        material: str,
        quantity: int,
        valuation_price: float,
        currency: str,
        price_unit: int,
        delivery_date: str,
        plant: str,
        purchasing_group: str,
        purchasing_organization: str,
        company_code: str,
    ) -> dict[str, str | int]:
        self._fill_label("Material", material)
        self._fill_label("Bewertungspreis", str(valuation_price))
        self._fill_label("Preiseinheit", str(price_unit))
        self._fill_label("Anforderungsmenge", str(quantity))
        self._fill_label("Lieferdatum", delivery_date)
        self._fill_label("Werk", plant)
        self._fill_label("Einkäufergruppe", purchasing_group)
        self._fill_label("EinkOrganisation", purchasing_organization)
        self._fill_label("Buchungskreis", company_code)
        self._page.locator("#application-PurchaseRequisition-create-component---Freetext--btnCart").click()
        self._page.get_by_role("button", name="Bestellen").click()
        requisition_link = self._page.locator("#idPRNoLinkId")
        expect(requisition_link).to_be_visible()
        return {
            "purchase_requisition": requisition_link.inner_text(),
            "material": material,
            "quantity": quantity,
        }

    def _fill_label(self, label: str, value: str) -> None:
        field = self._page.get_by_label(label, exact=False)
        field.fill(value)
