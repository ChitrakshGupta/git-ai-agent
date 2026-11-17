"""Base version of cart.py (commit this to main first)."""


def subtotal(items: list[dict]) -> float:
    return sum(item["price"] * item["qty"] for item in items)
