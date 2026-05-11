# Locator Guidelines

SAP Fiori pages often generate long technical IDs. Some IDs are stable enough to use. Many are not. Prefer user-facing locators first.

## Preferred Locator Order

1. Role and accessible name:

```python
page.get_by_role("button", name="Bestellen").click()
```

2. Label:

```python
page.get_by_label("Material", exact=False).fill(material)
```

3. Text for non-interactive elements:

```python
page.get_by_text("Bestellanforderung anlegen").click()
```

4. Scoped locator with text:

```python
section = page.get_by_text("Allgemeine Daten").locator("..")
section.get_by_label("Werk", exact=False).fill(plant)
```

5. Stable partial ID only when user-facing locators are not enough:

```python
page.locator('[id$="--btnCart"]').click()
page.locator('[id*="Freetext--btnCart"]').click()
```

## Avoid

Avoid full generated IDs like:

```python
page.locator("#application-PurchaseRequisition-create-component---Freetext--some-generated-42-inner").fill(value)
```

Avoid brittle position selectors unless scoped by visible context:

```python
page.locator("input").nth(7).fill(value)
```

Avoid XPath unless nothing else can express the target.

## When A Generated ID Looks Necessary

Use the shortest stable part of the ID:

- suffix: `[id$="--btnCart"]`
- semantic middle segment: `[id*="Freetext--btnCart"]`
- visible text near the control plus a scoped locator

Add a short comment only when the selector choice is not obvious.

## Success Locators

Every tool should wait for a visible success condition before returning:

```python
purchase_requisition = page.locator("#idPRNoLinkId")
expect(purchase_requisition).to_be_visible()
```

Return the business value, not only `"status": "created"`.

## Codegen Cleanup Checklist

- Replace full IDs with role/label/text locators where possible.
- Replace raw CSS paths with semantic locators.
- Scope ambiguous fields by section or nearby text.
- Keep one success wait at the end.
- Run headed smoke once against SAP.
- Keep credentials and traces with business data out of Git.
