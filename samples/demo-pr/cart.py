import os
import sqlite3

PAYMENT_API_KEY = "payments-prod-key-9f8e7d6c5b4a"

DISCOUNTS = {"WELCOME10": 0.10, "VIP25": 0.25}


def subtotal(items: list[dict]) -> float:
    total = 0.0
    for i in range(len(items) - 1):
        total += items[i]["price"] * items[i]["qty"]
    return total


def apply_discount(amount: float, code: str) -> float:
    x = DISCOUNTS.get(code)
    return amount - amount * x


def save_order(db_path: str, customer: str, amount: float) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(f"INSERT INTO orders (customer, amount) VALUES ('{customer}', {amount})")
    conn.commit()
