"""SAP WebGUI SE16 extraction and ABAP-list parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from erp_sap_export.specs import FIELD_TITLES, SelectionRange, TableRequest


@dataclass(frozen=True)
class AbapListItem:
    text: str
    x: int
    y: int


@dataclass(frozen=True)
class WebGuiCredentials:
    username: str
    password: str
    webgui_url: str


def webgui_url_from_login_url(login_url: str) -> str:
    parsed = urlparse(login_url)
    query = parse_qs(parsed.query)
    client = query.get("sap-client", ["204"])[0]
    language = query.get("sap-language", ["de"])[0]
    return f"{parsed.scheme}://{parsed.netloc}/sap/bc/gui/sap/its/webgui?~transaction={quote('*SE16')}&sap-client={client}&sap-language={language}"


def parse_abap_list(items: list[AbapListItem]) -> list[dict[str, str]]:
    rows = _group_by_y(items)
    if not rows:
        return []
    header_y, headers = _find_header_row(rows)
    if not headers:
        return []
    output: list[dict[str, str]] = []
    for y, row_items in rows:
        if y <= header_y:
            continue
        mapped = _map_row(headers, row_items)
        if mapped and any(value for value in mapped.values()):
            output.append(mapped)
    return output


class Se16Client:
    def __init__(
        self,
        credentials: WebGuiCredentials,
        *,
        headed: bool = False,
        viewport_width: int = 10_000,
        viewport_height: int = 6_000,
    ) -> None:
        self.credentials = credentials
        self.headed = headed
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

    def probe(self, tables: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {"webgui": False, "se16": False, "tables": {}}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed)
            context = browser.new_context(viewport={"width": self.viewport_width, "height": self.viewport_height})
            page = context.new_page()
            try:
                self._open_se16(page)
                result["webgui"] = True
                result["se16"] = _has_table_name_input(page)
            finally:
                page.close()
                browser.close()
            for table in tables:
                browser = playwright.chromium.launch(headless=not self.headed)
                context = browser.new_context(viewport={"width": self.viewport_width, "height": self.viewport_height})
                page = context.new_page()
                try:
                    result["tables"][table] = self._probe_table(page, table)
                finally:
                    page.close()
                    browser.close()
        return result

    def extract(self, request: TableRequest) -> list[dict[str, str]]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed)
            context = browser.new_context(viewport={"width": self.viewport_width, "height": self.viewport_height})
            page = context.new_page()
            try:
                self._open_table_selection(page, request.table)
                self._apply_selection(page, request.selection)
                self._set_standard_limits(page, request.max_rows)
                self._execute(page)
                self._wait_for_result(page, request.table)
                return parse_abap_list(_extract_abap_items(page))
            finally:
                browser.close()

    def _probe_table(self, page: Page, table: str) -> dict[str, Any]:
        self._open_table_selection(page, table)
        body_text = _body_text(page)
        return {
            "selection_screen": "Maximale Trefferzahl" in body_text,
            "field_selection_prompt": "Felder für Selektion auswählen" in body_text,
            "not_authorized": "nicht berechtigt" in body_text.lower() or "keine berechtigung" in body_text.lower(),
            "title": page.title(),
        }

    def _open_table_selection(self, page: Page, table: str) -> None:
        self._open_se16(page)
        table_input = page.locator('input[title="Tabellenname"]').first
        table_input.fill(table)
        table_input.press("Enter")
        page.wait_for_timeout(3_000)
        if "Felder für Selektion auswählen" in _body_text(page):
            self._execute(page)
            page.wait_for_timeout(3_000)
        if "Maximale Trefferzahl" not in _body_text(page):
            raise RuntimeError(f"SE16 did not open a selection screen for table {table}")

    def _open_se16(self, page: Page) -> None:
        page.goto(self.credentials.webgui_url, wait_until="load", timeout=45_000)
        self._login_if_needed(page)
        page.wait_for_timeout(2_000)
        if not _has_table_name_input(page):
            raise RuntimeError("SAP WebGUI SE16 table-name field is not visible")

    def _login_if_needed(self, page: Page) -> None:
        if page.locator("#sap-user").is_visible(timeout=10_000):
            page.locator("#sap-user").fill(self.credentials.username)
            page.locator("#sap-password").fill(self.credentials.password)
            page.locator("#sap-password").press("Enter")
            page.wait_for_load_state("load", timeout=45_000)

    def _apply_selection(self, page: Page, selection: list[SelectionRange]) -> None:
        for item in selection:
            self._fill_range(page, item)

    def _fill_range(self, page: Page, item: SelectionRange) -> None:
        locators = _selection_inputs(page, item.field)
        if not locators:
            raise RuntimeError(f"Could not locate SE16 selection field {item.field}")
        locators[0].fill(item.low)
        if item.high is not None:
            if len(locators) < 2:
                raise RuntimeError(f"Could not locate SE16 high-value field {item.field}")
            locators[1].fill(item.high)

    def _set_standard_limits(self, page: Page, max_rows: int | None) -> None:
        _fill_first_visible(page, 'input[title="Listbreite des Data Browser"]', "1023")
        if max_rows is not None:
            _fill_first_visible(page, 'input[title="Maximal selektierte Einträge"]', str(max_rows))

    def _execute(self, page: Page) -> None:
        try:
            page.get_by_title("Ausführen").first.click(timeout=3_000)
        except Exception:
            page.keyboard.press("F8")

    def _wait_for_result(self, page: Page, table: str) -> None:
        page.wait_for_timeout(5_000)
        body_text = _body_text(page)
        if "nicht berechtigt" in body_text.lower() or "keine berechtigung" in body_text.lower():
            raise RuntimeError(f"Not authorized to read SAP table {table}")
        if "Keine Daten" in body_text or "Keine Einträge" in body_text:
            return
        if f"Tabelle {table}" not in body_text and table not in page.title():
            raise RuntimeError(f"SE16 result for {table} did not load")


def _extract_abap_items(page: Page) -> list[AbapListItem]:
    payload = page.evaluate(
        """() => Array.from(document.querySelectorAll('.lsAbapList__item')).map((el) => {
            const rect = el.getBoundingClientRect();
            return {
                text: (el.innerText || el.textContent || '').trim(),
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            };
        }).filter((item) => item.text && item.width > 0 && item.height > 0)"""
    )
    return [AbapListItem(text=str(item["text"]), x=int(item["x"]), y=int(item["y"])) for item in payload]


def _group_by_y(items: list[AbapListItem]) -> list[tuple[int, list[AbapListItem]]]:
    groups: list[tuple[int, list[AbapListItem]]] = []
    for item in sorted(items, key=lambda candidate: (candidate.y, candidate.x)):
        if not groups or abs(groups[-1][0] - item.y) > 8:
            groups.append((item.y, [item]))
        else:
            groups[-1][1].append(item)
    return [(y, sorted(row_items, key=lambda candidate: candidate.x)) for y, row_items in groups]


def _find_header_row(rows: list[tuple[int, list[AbapListItem]]]) -> tuple[int, list[tuple[str, int]]]:
    best: tuple[int, list[tuple[str, int]]] = (0, [])
    for y, row_items in rows:
        headers = [(item.text.strip(), item.x) for item in row_items if _looks_like_field_name(item.text)]
        if len(headers) > len(best[1]):
            best = (y, headers)
    return best


def _map_row(headers: list[tuple[str, int]], row_items: list[AbapListItem]) -> dict[str, str]:
    mapped: dict[str, list[str]] = {header: [] for header, _x in headers}
    sorted_headers = sorted(headers, key=lambda item: item[1])
    for item in row_items:
        header = _header_for_x(sorted_headers, item.x)
        if header is not None:
            mapped[header].append(item.text)
    return {header: " ".join(parts).strip() for header, parts in mapped.items() if parts}


def _header_for_x(headers: list[tuple[str, int]], x: int) -> str | None:
    current: str | None = None
    for header, header_x in headers:
        if x >= header_x:
            current = header
        else:
            break
    return current


def _looks_like_field_name(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 30:
        return False
    return text.replace("_", "").isalnum() and text.upper() == text and any(char.isalpha() for char in text)


def _selection_inputs(page: Page, field: str):
    for title in FIELD_TITLES.get(field, []):
        locator = page.locator(f'input[title="{title}"]')
        count = locator.count()
        if count:
            return [locator.nth(index) for index in range(count)]
    return _selection_inputs_by_field_label(page, field)


def _selection_inputs_by_field_label(page: Page, field: str):
    payload = page.evaluate(
        """(fieldName) => {
            const labels = Array.from(document.querySelectorAll('.lsAbapList__item, span, div'))
                .map((el) => {
                    const rect = el.getBoundingClientRect();
                    return { text: (el.innerText || el.textContent || '').trim(), x: rect.x, y: rect.y };
                })
                .filter((item) => item.text === fieldName);
            const label = labels[0];
            if (!label) return [];
            return Array.from(document.querySelectorAll('input[role="textbox"], input[type="text"]'))
                .map((el, index) => {
                    const rect = el.getBoundingClientRect();
                    return { index, x: rect.x, y: rect.y, visible: rect.width > 0 && rect.height > 0 };
                })
                .filter((item) => item.visible && Math.abs(item.y - label.y) < 12 && item.x > label.x)
                .sort((a, b) => a.x - b.x)
                .map((item) => item.index);
        }""",
        field,
    )
    locators = page.locator('input[role="textbox"], input[type="text"]')
    return [locators.nth(int(index)) for index in payload]


def _fill_first_visible(page: Page, selector: str, value: str) -> None:
    locator = page.locator(selector)
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible(timeout=500):
                candidate.fill(value)
                return
        except PlaywrightTimeoutError:
            continue


def _has_table_name_input(page: Page) -> bool:
    try:
        return page.locator('input[title="Tabellenname"]').first.is_visible(timeout=5_000)
    except PlaywrightTimeoutError:
        return False


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3_000)
    except Exception:
        return ""
