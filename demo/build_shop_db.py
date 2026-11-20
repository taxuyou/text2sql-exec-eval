"""Build the tiny demo database ``demo/shop.sqlite`` (an online shop).

All data is synthetic. Rows are generated with a small linear congruential
generator instead of :mod:`random`, so the contents are identical on every
Python version and platform.

Usage::

    python demo/build_shop_db.py            # writes demo/shop.sqlite
    python demo/build_shop_db.py out.sqlite # writes somewhere else
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("shop.sqlite")

SCHEMA = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories (category_id),
    price       INTEGER NOT NULL            -- KRW
);

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,              -- not unique on purpose
    city        TEXT NOT NULL,
    joined_at   TEXT NOT NULL               -- YYYY-MM-DD
);

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date  TEXT NOT NULL,              -- YYYY-MM-DD
    status      TEXT NOT NULL
        CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);

CREATE TABLE order_items (
    order_id    INTEGER NOT NULL REFERENCES orders (order_id),
    product_id  INTEGER NOT NULL REFERENCES products (product_id),
    quantity    INTEGER NOT NULL,
    unit_price  INTEGER NOT NULL,           -- KRW, price at order time
    PRIMARY KEY (order_id, product_id)
);
"""

CATEGORIES = [
    (1, "Electronics"),
    (2, "Books"),
    (3, "Home & Kitchen"),
    (4, "Sports"),
    (5, "Beauty"),
    (6, "Gift Cards"),  # intentionally has no products (LEFT JOIN demo)
]

PRODUCTS = [
    (1, "Wireless Earbuds", 1, 89000),
    (2, "Bluetooth Speaker", 1, 59000),
    (3, "USB-C Charger", 1, 25000),
    (4, "Mechanical Keyboard", 1, 129000),
    (5, "27-inch Monitor", 1, 329000),
    (6, "Webcam", 1, 69000),
    (7, "Python Data Analysis", 2, 32000),
    (8, "SQL Cookbook", 2, 28000),
    (9, "Korean Short Stories", 2, 15000),
    (10, "Machine Learning Basics", 2, 36000),
    (11, "Travel Guide: Jeju", 2, 18000),
    (12, "Coffee Grinder", 3, 45000),
    (13, "Stainless Pan", 3, 39000),
    (14, "Rice Cooker", 3, 159000),
    (15, "Glass Storage Set", 3, 27000),
    (16, "Electric Kettle", 3, 35000),
    (17, "Yoga Mat", 4, 22000),
    (18, "Running Shoes", 4, 99000),
    (19, "Dumbbell Set", 4, 79000),
    (20, "Water Bottle", 4, 12000),
    (21, "Sunscreen", 5, 18000),
    (22, "Hand Cream", 5, 9000),
    (23, "Face Mask Pack", 5, 15000),
    (24, "Hair Dryer", 5, 65000),
]

# (customer_id, name, city). Two different customers share the name
# "Kim Minjun" on purpose: grouping by name instead of id is a classic bug.
CUSTOMERS = [
    (1, "Lee Seoyeon", "Seoul"),
    (2, "Park Jihoon", "Busan"),
    (3, "Kim Minjun", "Seoul"),
    (4, "Choi Yuna", "Incheon"),
    (5, "Jung Hyunwoo", "Daegu"),
    (6, "Kang Soyeon", "Seoul"),
    (7, "Yoon Taeyang", "Daejeon"),
    (8, "Jang Hayoon", "Busan"),
    (9, "Lim Dohyun", "Seoul"),
    (10, "Han Jiwoo", "Gwangju"),
    (11, "Oh Seojun", "Incheon"),
    (12, "Seo Yerin", "Seoul"),
    (13, "Shin Joonho", "Busan"),
    (14, "Kwon Nayeon", "Daegu"),
    (15, "Hwang Siwoo", "Seoul"),
    (16, "Ahn Chaewon", "Incheon"),
    (17, "Kim Minjun", "Busan"),
    (18, "Song Eunji", "Seoul"),
    (19, "Yoo Jaehyun", "Daejeon"),
    (20, "Hong Subin", "Seoul"),
    (21, "Jeon Woojin", "Busan"),
    (22, "Ko Haneul", "Incheon"),
    (23, "Moon Sihyun", "Daegu"),
    (24, "Yang Minseo", "Seoul"),
    (25, "Son Geonwoo", "Gwangju"),
    (26, "Bae Jiyoung", "Busan"),
    (27, "Baek Seungmin", "Seoul"),
    (28, "Heo Dahye", "Incheon"),
    (29, "Nam Junseo", "Daegu"),
    (30, "Noh Yujin", "Daejeon"),
]

N_ORDERS = 60
SEED = 20260114

# Weighted status list: 60% delivered, 15% shipped, 10% pending, 15% cancelled.
STATUS_BAG = ["delivered"] * 12 + ["shipped"] * 3 + ["pending"] * 2 + ["cancelled"] * 3


class Lcg:
    """Minimal deterministic PRNG (glibc-style LCG constants)."""

    def __init__(self, seed: int) -> None:
        self.state = seed % 2**31

    def next(self) -> int:
        self.state = (1103515245 * self.state + 12345) % 2**31
        return self.state >> 8  # drop low bits, which have short periods

    def randint(self, lo: int, hi: int) -> int:
        return lo + self.next() % (hi - lo + 1)

    def choice(self, items: list):
        return items[self.next() % len(items)]


def generate_rows() -> dict[str, list[tuple]]:
    rng = Lcg(SEED)

    customers = []
    for cid, name, city in CUSTOMERS:
        joined = date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))
        customers.append((cid, name, city, joined.isoformat()))

    price = {pid: p for pid, _, _, p in PRODUCTS}
    orders, items = [], []
    for oid in range(1, N_ORDERS + 1):
        cid = rng.randint(1, len(CUSTOMERS))
        day = date(2026, 1, 1) + timedelta(days=rng.randint(0, 180))
        orders.append((oid, cid, day.isoformat(), rng.choice(STATUS_BAG)))

        n_lines = rng.randint(1, 3)
        chosen: list[int] = []
        while len(chosen) < n_lines:
            pid = rng.randint(1, len(PRODUCTS))
            if pid not in chosen:
                chosen.append(pid)
        for pid in chosen:
            items.append((oid, pid, rng.randint(1, 3), price[pid]))

    return {
        "categories": CATEGORIES,
        "products": PRODUCTS,
        "customers": customers,
        "orders": orders,
        "order_items": items,
    }


def build(path: str | Path = DEFAULT_PATH) -> Path:
    """(Re)create the demo database at *path* and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        for table, rows in generate_rows().items():
            marks = ", ".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        conn.commit()
    finally:
        conn.close()
    return path


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    out = build(argv[0] if argv else DEFAULT_PATH)
    conn = sqlite3.connect(out)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("categories", "products", "customers", "orders", "order_items")
    }
    conn.close()
    summary = ", ".join(f"{t}={n}" for t, n in counts.items())
    print(f"wrote {out} ({summary})")


if __name__ == "__main__":
    main()
